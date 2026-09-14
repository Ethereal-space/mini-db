# 成员 A 前端接口快照

本次工作区只有教学文档，没有团队基线。因此本目录按手册初始化 A 所需的最小公共契约，不声称包含 B/C/D 的完整共同基线。整合前四人需统一此文件及 `contracts.sha256`，已有其他基线时先显式比对字段，不能覆盖或静默改名。

## 调用方式

```python
from minidb.frontend import Frontend, Lexer, Parser

tokens = Lexer(sql).tokenize()
statements = Parser(tokens).parse()
# 生产装配优先使用下列端口，内部会创建全新的 Lexer/Parser。
events = []
statements = Frontend(on_trace=events.append).parse(sql)
```

`FrontendPort.parse(source: str) -> list[Statement]`。一次解析完整脚本；语法或词法失败抛领域错误，不返回已解析的前半段。Frontend 不保存上一次调用的 Token 或结果，无 I/O 副作用。

`Parser(tokens)` 用于直接测试 Token。必须有且仅有末尾一个 EOF，非法内部输入抛 ValueError，而不是伪装成用户 SQL 语法错误。Parser 是单次消费对象；重复 SQL 调用使用 Frontend。

## 冻结数据

| 文件 | 类型 |
|---|---|
| source.py | Position(offset,line,column)、Span(start,end) |
| tokens.py | TokenKind、Token(kind,lexeme,value,span) |
| ast.py | Literal、Identifier、UnaryExpr、BinaryExpr、ColumnDef、四种 Statement |
| errors.py | MiniDBError(stage,code,message,span,context)、LexicalError、SyntaxError |
| results.py | TraceEvent(stage,detail) |
| ports.py | FrontendPort |

所有 AST、Token、Span、Position、TraceEvent 均为 frozen dataclass；前端产物集合字段为 tuple。Statement 返回的最外层批次按端口规定为 list。名称必须是 Identifier 节点，不能改成裸字符串。完整字段见源码；字段顺序也由契约测试固定。

## Trace 与格式

`Frontend(on_trace: Callable[[TraceEvent],None] | None = None)`。词法成功后发一次 TOKEN，整批语法成功后发一次 AST。语法失败可能只有 TOKEN；词法失败无成功事件。空批次也输出可观察的 EOF 与空 AST。事件 detail 为确定性 JSON 文本，不包含 Python 对象地址。

`format_tokens(tokens)`、`format_ast(node_or_statements)` 返回 JSON 字符串。`ast_to_data` 返回 JSON 基本值组成的数据；每个节点是 `{kind,fields,span}`，Span 含 start/end 坐标。字段按 dataclass 定义顺序输出，Unicode 保留，字符串控制字符转义。格式化不重新解析，不修改 AST。

只有本批次 TOKEN/AST 事件由 A 产生。最终 integration 负责把它们附到该批次的第一个 ExecutionResult，且不能复制到第二、第三条结果；A 无权生成 SEMANTIC、PLAN、EXECUTION 事件。回调抛出的内部异常原样传播，不吞错。

## 错误

LexicalError(code,message,span,context=None) 固定阶段 LEXICAL；SyntaxError 同理为 SYNTAX。语法 context 至少有 actual（TokenKind 字符串）、lexeme、expected（有序 tuple）。用户展示用 str(error)，定位或自动测试读取字段。

词法错误码：ILLEGAL_CHARACTER、IDENTIFIER_TOO_LONG、INVALID_NUMBER、UNTERMINATED_STRING、UNTERMINATED_COMMENT。

语法错误码：UNEXPECTED_TOKEN、CHAINED_COMPARISON、NESTING_LIMIT。缺表列、类型和插入列重排均由 B 完成；不要把这类错误补到 Lexer/Parser。

## 整合边界

成员 A 的生产模块只导入标准库、自身包及 contracts。B 可用手工 AST 独立测试，再接受 A 的 Statement。C/D 具体实现未创建。`demo/frontend_demo.py` 是 A 的独立展示脚本，不是成员 D 的数据库 CLI。没有数据库文件被创建或修改。
