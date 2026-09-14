"""核心 SQL 的手写递归下降 Parser，以及无状态 FrontendPort 实现。"""

from collections.abc import Callable, Collection, Sequence
from dataclasses import replace

from minidb.contracts.ast import (
    BinaryExpr, ColumnDef, CreateTableStmt, DeleteStmt, Expr, Identifier,
    InsertStmt, Literal, SelectStmt, Statement, UnaryExpr,
)
from minidb.contracts.errors import MiniDBError, SyntaxError
from minidb.contracts.extensions import ExtensionStatement
from minidb.contracts.results import TraceEvent
from minidb.contracts.source import Span
from minidb.contracts.tokens import ALL_KEYWORDS, Token, TokenKind as K

from .lexer import Lexer


# AST 使用统一比较符，输入中的 ==、<> 分别转换为 =、!=。
COMPARISONS = {
    K.EQ: "=", K.NE: "!=", K.LT: "<", K.LE: "<=", K.GT: ">", K.GE: ">=",
}
# 各类语法成分允许的起始 Token，同时用于生成“期望符号”错误提示。
LITERAL_START = (K.INTEGER, K.MINUS, K.STRING, K.FLOAT)
PRIMARY_START = (K.IDENTIFIER, *LITERAL_START, K.LEFT_PAREN)
EXPRESSION_START = (*PRIMARY_START, K.NOT)
STATEMENT_START = (K.CREATE, K.INSERT, K.SELECT, K.DELETE)
SUPPORTED_EXTENSIONS = frozenset({"update", "order_limit", "distinct"})
ParsedStatement = Statement | ExtensionStatement


def _extension_set(features: Collection[str]) -> frozenset[str]:
    if isinstance(features, str):
        raise TypeError("enabled_extensions 需要名称集合，例如 {'update'}，不能传单个字符串")
    enabled = frozenset(features)
    unknown = enabled - SUPPORTED_EXTENSIONS
    if unknown:
        raise MiniDBError("UNSUPPORTED", "UNKNOWN_EXTENSION",
                          f"未知前端扩展：{', '.join(sorted(map(str, unknown)))}")
    return enabled


class Parser:
    """Token 下标只前进；不查询元数据、不求值、不写磁盘。"""

    def __init__(self, tokens: Sequence[Token], *, max_nesting: int = 64,
                 enabled_extensions: Collection[str] = ()):
        self.tokens = tuple(tokens)
        if not self.tokens or self.tokens[-1].kind != K.EOF:
            raise ValueError("Parser 的 Token 序列必须以 EOF 结束")
        if any(t.kind == K.EOF for t in self.tokens[:-1]):
            raise ValueError("EOF 只能在 Token 序列末尾出现一次")
        if not 1 <= max_nesting <= 64:
            raise ValueError("max_nesting 必须在 1 到 64 之间")
        # index 指向当前 Token；nesting 记录当前进入了多少层表达式括号。
        self.index = 0
        self.nesting = 0
        self.max_nesting = max_nesting
        self.enabled_extensions = _extension_set(enabled_extensions)

    def peek(self) -> Token:
        return self.tokens[self.index]

    def consume(self) -> Token:
        token = self.peek()
        # EOF 保持在末尾，防止缺少符号时继续前进而产生下标越界。
        if token.kind != K.EOF:
            self.index += 1
        return token

    def _unexpected(self, expected: Sequence[K | str], *, code: str = "UNEXPECTED_TOKEN",
                    reason: str = "") -> SyntaxError:
        # 集中组织实际符号、期望集合和源码位置，让各解析函数的报错一致。
        token = self.peek()
        expected_names = tuple(dict.fromkeys(kind.name if isinstance(kind, K) else kind
                                            for kind in expected))
        actual = "EOF" if token.kind == K.EOF else repr(token.lexeme)
        message = f"遇到 {actual} ({token.kind.name})"
        if expected_names:
            message += f"；期望 {' | '.join(expected_names)}"
        if reason:
            message = f"{reason}；{message}"
        return SyntaxError(code, message, token.span, {
            "actual": token.kind.name, "lexeme": token.lexeme,
            "expected": expected_names,
        })

    def expect(self, *kinds: K) -> Token:
        # 文法要求某个符号必须出现时使用此方法，匹配成功才消费 Token。
        if self.peek().kind not in kinds:
            raise self._unexpected(kinds)
        return self.consume()

    def _span_since(self, first: Token) -> Span:
        # 语句范围到最后已消费的 Token 为止，不包含尚未消费的分号。
        return Span(first.span.start, self.tokens[self.index - 1].span.end)

    def parse(self) -> list[ParsedStatement]:
        statements = []
        while self.peek().kind != K.EOF:
            if self.peek().kind == K.SEMICOLON:
                # 连续分号代表空语句，跳过即可，不生成空 AST。
                self.consume()
                continue
            statement = self.parse_statement()
            # 最后一条可以直接接 EOF，其余语句之间必须用分号分隔。
            if self.peek().kind not in (K.SEMICOLON, K.EOF):
                raise self._unexpected((K.SEMICOLON, K.EOF), reason="语句之间需要分号，不支持额外后缀")
            statements.append(statement)
        return statements

    def parse_statement(self) -> ParsedStatement:
        if self._at_word("update"):
            self._require_extension("update")
            return self.parse_update()
        # 根据语句的第一个关键字分派到对应的递归下降入口。
        methods = {
            K.CREATE: self.parse_create_table,
            K.INSERT: self.parse_insert,
            K.SELECT: self.parse_select,
            K.DELETE: self.parse_delete,
        }
        method = methods.get(self.peek().kind)
        if method is None:
            raise self._unexpected(STATEMENT_START, reason="核心仅支持 CREATE、INSERT、SELECT、DELETE")
        return method()

    def _at_word(self, word: str) -> bool:
        # 关键字文本到种别的映射来自冻结契约；字符串字面量不会误匹配。
        token = self.peek()
        return token.kind is ALL_KEYWORDS.get(word.casefold())

    def _expect_word(self, word: str) -> Token:
        if not self._at_word(word):
            raise self._unexpected((word.upper(),))
        return self.consume()

    def _require_extension(self, feature: str) -> None:
        if feature not in self.enabled_extensions:
            raise self._unexpected((), code="EXTENSION_DISABLED",
                                   reason=f"扩展 {feature} 未启用，请显式配置 enabled_extensions")

    def parse_assignment(self) -> dict:
        from .formatter import ast_to_data

        column = self.parse_identifier()
        self.expect(K.EQ)
        expression = self.parse_expression()
        return {"column": ast_to_data(column), "expr": ast_to_data(expression)}

    def parse_update(self) -> ExtensionStatement:
        from .formatter import ast_to_data

        first = self._expect_word("update")
        table = self.parse_identifier()
        self._expect_word("set")
        assignments = list(self._comma_list(self.parse_assignment,
                                            (K.WHERE, K.SEMICOLON, K.EOF)))
        where = self.parse_where_optional()
        return ExtensionStatement("update", 1, {
            "table": ast_to_data(table), "assignments": assignments,
            "where": ast_to_data(where),
        }, self._span_since(first))

    def parse_identifier(self) -> Identifier:
        # 名称和位置一起进入 AST，后续成员 B 才能准确定位未知表列。
        token = self.expect(K.IDENTIFIER)
        return Identifier(token.value, token.span)

    def _comma_list(self, item: Callable, terminators: tuple[K, ...]) -> tuple:
        """至少一项；缺分隔符时同时报告逗号和可能的结束符。"""
        items = [item()]
        while True:
            if self.peek().kind == K.COMMA:
                self.consume()
                # 逗号之后必须还有一项，因此尾随逗号不能被静默接受。
                items.append(item())
            elif self.peek().kind in terminators:
                return tuple(items)
            else:
                raise self._unexpected((K.COMMA, *terminators))

    def parse_column_def(self) -> ColumnDef:
        # 一项列定义由“列名 + 类型”组成，基础存储类型只有 INT、VARCHAR。
        name = self.parse_identifier()
        dtype = self.expect(K.INT, K.VARCHAR)
        return ColumnDef(name, dtype.kind.name, Span(name.span.start, dtype.span.end))

    def parse_create_table(self) -> CreateTableStmt:
        # 按 CREATE TABLE 表名(列定义,...) 的顺序消费输入并构造建表节点。
        first = self.expect(K.CREATE)
        self.expect(K.TABLE)
        name = self.parse_identifier()
        self.expect(K.LEFT_PAREN)
        columns = self._comma_list(self.parse_column_def, (K.RIGHT_PAREN,))
        self.expect(K.RIGHT_PAREN)
        return CreateTableStmt(name, columns, self._span_since(first))

    def parse_literal(self) -> Literal:
        token = self.expect(*LITERAL_START)
        if token.kind == K.MINUS:
            # 负整数由负号和整数 Token 合并，节点位置同时覆盖负号和数字。
            number = self.expect(K.INTEGER)
            return Literal(-number.value, Span(token.span.start, number.span.end))
        return Literal(token.value, token.span)

    def parse_insert(self) -> InsertStmt:
        first = self.expect(K.INSERT)
        self.expect(K.INTO)
        table = self.parse_identifier()
        columns = None
        # None 表示省略列列表；显式列表保留原顺序，不能在这里重排值。
        if self.peek().kind == K.LEFT_PAREN:
            self.consume()
            columns = self._comma_list(self.parse_identifier, (K.RIGHT_PAREN,))
            self.expect(K.RIGHT_PAREN)
        self.expect(K.VALUES)
        self.expect(K.LEFT_PAREN)
        values = self._comma_list(self.parse_literal, (K.RIGHT_PAREN,))
        self.expect(K.RIGHT_PAREN)
        return InsertStmt(table, columns, values, self._span_since(first))

    def parse_projection(self) -> tuple[Identifier, ...] | None:
        # 契约用 None 表示 SELECT *，普通列列表保留顺序和重复列。
        if self.peek().kind == K.STAR:
            self.consume()
            return None
        return self._comma_list(self.parse_identifier, (K.FROM,))

    def parse_where_optional(self) -> Expr | None:
        # SELECT 和 DELETE 共用此入口，无 WHERE 时返回 None。
        if self.peek().kind == K.WHERE:
            self.consume()
            return self.parse_expression()
        return None

    def parse_select(self) -> SelectStmt | ExtensionStatement:
        first = self.expect(K.SELECT)
        distinct = self._at_word("distinct")
        if distinct:
            self._require_extension("distinct")
            self.consume()
            if self.peek().kind not in (K.IDENTIFIER, K.STAR):
                raise self._unexpected((K.IDENTIFIER, K.STAR))
        # 查询的基础结构是投影、数据表和可选条件；此处只构树，不读取数据。
        columns = self.parse_projection()
        self.expect(K.FROM)
        table = self.parse_identifier()
        where = self.parse_where_optional()
        query = SelectStmt(table, columns, where, self._span_since(first))
        if self._at_word("order") or self._at_word("limit"):
            if distinct:
                raise self._unexpected((K.SEMICOLON, K.EOF),
                                       code="UNSUPPORTED_COMBINATION",
                                       reason="distinct v1 不支持与 ORDER BY/LIMIT 组合")
            self._require_extension("order_limit")
            return self.parse_query_suffix(query, first)
        if distinct:
            from .formatter import ast_to_data

            return ExtensionStatement("distinct", 1, {"query": ast_to_data(query)},
                                      self._span_since(first))
        return query

    def parse_order_terms(self) -> list[dict]:
        from .formatter import ast_to_data

        terms = []
        while True:
            column = self.parse_identifier()
            direction = "ASC"
            if self._at_word("asc") or self._at_word("desc"):
                direction = self.consume().value.upper()
            terms.append({"column": ast_to_data(column), "direction": direction})
            if self.peek().kind != K.COMMA:
                return terms
            self.consume()

    def parse_limit(self) -> int:
        self._expect_word("limit")
        if self.peek().kind != K.INTEGER:
            raise self._unexpected((K.INTEGER,), code="INVALID_LIMIT",
                                   reason="LIMIT 需要非负整数")
        return self.consume().value

    def parse_query_suffix(self, query: SelectStmt, first: Token) -> ExtensionStatement:
        from .formatter import ast_to_data

        order_by = []
        limit = None
        if self._at_word("order"):
            self.consume()
            self._expect_word("by")
            order_by = self.parse_order_terms()
        if self._at_word("limit"):
            limit = self.parse_limit()
        if self._at_word("order") or self._at_word("limit"):
            raise self._unexpected((K.SEMICOLON, K.EOF), code="INVALID_CLAUSE_ORDER",
                                   reason="ORDER BY 必须在 LIMIT 之前，子句不可重复")
        return ExtensionStatement("order_limit", 1, {
            "query": ast_to_data(query), "order_by": order_by, "limit": limit,
        }, self._span_since(first))

    def parse_delete(self) -> DeleteStmt:
        # DELETE 无 WHERE 是合法的全表删除语法，具体删除由执行器完成。
        first = self.expect(K.DELETE)
        self.expect(K.FROM)
        table = self.parse_identifier()
        where = self.parse_where_optional()
        return DeleteStmt(table, where, self._span_since(first))

    def parse_expression(self) -> Expr:
        # 从结合最松的 OR 层进入，依次下探到 AND、NOT、比较和基本项。
        return self.parse_or()

    def parse_or(self) -> Expr:
        # 先完成 AND 子树，保证 a OR b AND c 解析为 a OR (b AND c)。
        left = self.parse_and()
        while self.peek().kind == K.OR:
            self.consume()
            right = self.parse_and()
            left = BinaryExpr("OR", left, right, Span(left.span.start, right.span.end))
        return left

    def parse_and(self) -> Expr:
        left = self.parse_not()
        while self.peek().kind == K.AND:
            self.consume()
            right = self.parse_not()
            # 新节点复用已有左树，使同级的多个 AND 按从左到右的顺序结合。
            left = BinaryExpr("AND", left, right, Span(left.span.start, right.span.end))
        return left

    def parse_not(self) -> Expr:
        # 显式保存 NOT 栈，等价于 not_expr 的右递归，避免长 NOT 链耗尽调用栈。
        prefixes = []
        while self.peek().kind == K.NOT:
            prefixes.append(self.consume())
        if self.peek().kind not in PRIMARY_START:
            raise self._unexpected(EXPRESSION_START)
        # 先得到完整比较，再包上 NOT：NOT age=18 应是 NOT(age=18)。
        operand = self.parse_comparison()
        for token in reversed(prefixes):
            operand = UnaryExpr("NOT", operand, Span(token.span.start, operand.span.end))
        return operand

    def parse_comparison(self) -> Expr:
        left = self.parse_primary()
        if self.peek().kind in COMPARISONS:
            operator = self.consume()
            right = self.parse_primary()
            left = BinaryExpr(COMPARISONS[operator.kind], left, right,
                              Span(left.span.start, right.span.end))
            if self.peek().kind in COMPARISONS:
                # 基础文法不支持 a<b<c，第二个比较符处立即报错。
                raise self._unexpected((K.AND, K.OR, K.RIGHT_PAREN, K.SEMICOLON, K.EOF),
                                       code="CHAINED_COMPARISON", reason="不支持链式比较")
        return left

    def parse_primary(self) -> Expr:
        kind = self.peek().kind
        if kind == K.IDENTIFIER:
            return self.parse_identifier()
        if kind in LITERAL_START:
            return self.parse_literal()
        if kind == K.LEFT_PAREN:
            if self.nesting >= self.max_nesting:
                raise self._unexpected(PRIMARY_START, code="NESTING_LIMIT",
                                       reason=f"括号嵌套超过前端资源限制 {self.max_nesting} 层")
            first = self.consume()
            self.nesting += 1
            try:
                expr = self.parse_expression()
                last = self.expect(K.RIGHT_PAREN)
            finally:
                # 即使括号内解析失败，也要恢复嵌套计数。
                self.nesting -= 1
            # AST 不额外保存括号节点，但根表达式的范围需要包含外层括号。
            return replace(expr, span=Span(first.span.start, last.span.end))
        raise self._unexpected(PRIMARY_START)


class Frontend:
    def __init__(self, on_trace: Callable[[TraceEvent], None] | None = None, *,
                 enabled_extensions: Collection[str] = ()):
        self.on_trace = on_trace
        self.enabled_extensions = _extension_set(enabled_extensions)

    def parse(self, source: str) -> list[ParsedStatement]:
        # 每次建立新 Lexer/Parser；失败和上次调用均不会污染后续状态。
        from .formatter import format_ast, format_tokens

        tokens = Lexer(source).tokenize()
        if self.on_trace is not None:
            # 词法成功即可展示 Token；语法失败时不会发出成功的 AST 事件。
            self.on_trace(TraceEvent("TOKEN", format_tokens(tokens)))
        statements = Parser(tokens, enabled_extensions=self.enabled_extensions).parse()
        if self.on_trace is not None:
            # 只有整个脚本解析成功后才输出 AST，避免把部分成功误当全部成功。
            self.on_trace(TraceEvent("AST", format_ast(statements)))
        return statements
