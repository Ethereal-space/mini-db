"""按字符扫描 SQL，所有分支共用 advance，保证原文坐标一致。"""

import math

from minidb.contracts.errors import LexicalError
from minidb.contracts.source import Position, Span
from minidb.contracts.tokens import ALL_KEYWORDS, Token, TokenKind as K


# 关键字表来自冻结契约，避免根据 Enum.value（auto() 整数）反推文本。
# 关键字大小写不敏感，扩展关键字仍然先形成 Token，由 Parser 的开关决定是否接受。
KEYWORDS = dict(ALL_KEYWORDS)
# 把 SQL 中的运算符、括号和分隔符映射成解析器能够识别的种别。
SYMBOLS = {
    ">=": K.GE, "<=": K.LE, "!=": K.NE, "==": K.EQ, "<>": K.NE,
    "=": K.EQ, "<": K.LT, ">": K.GT, "+": K.PLUS, "-": K.MINUS,
    "*": K.STAR, "/": K.SLASH, "(": K.LEFT_PAREN, ")": K.RIGHT_PAREN,
    ",": K.COMMA, ";": K.SEMICOLON,
}


def _digit(char: str) -> bool:
    # 只接受 ASCII 数字，避免把其他语言的数字字符当成 SQL 数值。
    return "0" <= char <= "9"


def _name_start(char: str) -> bool:
    # 表名、列名的首字符只能是字母或下划线。
    return char == "_" or "a" <= char <= "z" or "A" <= char <= "Z"


def _name_part(char: str) -> bool:
    # 首字符之后允许出现数字，例如 student_1。
    return _name_start(char) or _digit(char)


def _decimal_integer(text: str) -> int:
    # 分块转换，避免 Python 的十进制字符串长度限制被误报为程序异常。
    value = 0
    for start in range(0, len(text), 9):
        chunk = text[start:start + 9]
        value = value * 10 ** len(chunk) + int(chunk)
    return value


class Lexer:
    def __init__(self, source: str):
        if not isinstance(source, str):
            raise TypeError("source 必须为 str")
        self.source = source
        # offset 是原字符串下标；line、column 是向用户展示的一基行列号。
        self.offset = 0
        self.line = 1
        self.column = 1

    def position(self) -> Position:
        # 保存当前坐标快照，后续游标移动不会改变这个位置对象。
        return Position(self.offset, self.line, self.column)

    def peek(self, distance: int = 0) -> str:
        # 只查看字符，不消费输入；向后查看可用于识别 >= 等双字符符号。
        index = self.offset + distance
        return self.source[index] if index < len(self.source) else ""

    def advance(self) -> str:
        """消费一个普通码点或一组 CRLF；返回真实消费的原文。"""
        start = self.offset
        char = self.peek()
        if not char:
            return ""
        self.offset += 1
        if char == "\r":
            # Windows 的 CRLF 占两个字符，但只算一次换行。
            if self.peek() == "\n":
                self.offset += 1
            self.line += 1
            self.column = 1
        elif char == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return self.source[start:self.offset]

    def _token(self, kind: K, start: Position, value: object) -> Token:
        # 原词素由源码切片得到；value 可以是解码后的值，二者不能混用。
        return Token(kind, self.source[start.offset:self.offset], value,
                     Span(start, self.position()))

    def _error(self, code: str, message: str, start: Position) -> LexicalError:
        return LexicalError(code, message, Span(start, self.position()))

    def scan_identifier(self) -> Token:
        start = self.position()
        while _name_part(self.peek()):
            self.advance()
        word = self.source[start.offset:self.offset]
        if len(word) > 64:
            raise self._error("IDENTIFIER_TOO_LONG", "标识符最长为 64 个字符", start)
        value = word.casefold()
        # 查不到关键字时就是普通标识符，不在词法阶段检查表或列是否存在。
        return self._token(KEYWORDS.get(value, K.IDENTIFIER), start, value)

    def scan_number(self) -> Token:
        start = self.position()
        while _digit(self.peek()):
            self.advance()
        floating = self.peek() == "."
        malformed = False
        if floating:
            self.advance()
            # 小数点后至少要有一位数字，因此 1. 不是合法数字。
            malformed = not _digit(self.peek())
            while _digit(self.peek()):
                self.advance()
        if self.peek() == "." or _name_part(self.peek()):
            # 1.2.3、12abc 必须整体报错，不能拆成多个看似合法的 Token。
            malformed = True
            while self.peek() == "." or _name_part(self.peek()):
                self.advance()
        text = self.source[start.offset:self.offset]
        if malformed:
            raise self._error("INVALID_NUMBER", f"非法数字 {text!r}，仅支持十进制整数及小数", start)
        if floating:
            value = float(text)
            if not math.isfinite(value):
                raise self._error("INVALID_NUMBER", "小数超出有限浮点数表示范围", start)
            return self._token(K.FLOAT, start, value)
        return self._token(K.INTEGER, start, _decimal_integer(text))

    def scan_string(self) -> Token:
        start = self.position()
        # 先跳过开引号；pieces 只保存实际字符串内容，不包含外层引号。
        self.advance()
        pieces = []
        while self.peek():
            if self.peek() == "'":
                self.advance()
                if self.peek() == "'":
                    # SQL 用两个连续单引号表示内容中的一个单引号。
                    self.advance()
                    pieces.append("'")
                else:
                    return self._token(K.STRING, start, "".join(pieces))
            else:
                pieces.append(self.advance())
        raise self._error("UNTERMINATED_STRING", "字符串未闭合，期望单引号", start)

    def scan_operator_or_comment(self) -> Token | None:
        start = self.position()
        pair = self.source[self.offset:self.offset + 2]
        if pair == "--":
            # 行注释在换行前结束，换行仍交给统一游标逻辑处理。
            while self.peek() and self.peek() not in "\r\n":
                self.advance()
            return None
        if pair == "/*":
            self.advance()
            self.advance()
            while self.peek():
                # 块注释不嵌套，遇到第一个结束标记便返回。
                if self.peek() == "*" and self.peek(1) == "/":
                    self.advance()
                    self.advance()
                    return None
                self.advance()
            raise self._error("UNTERMINATED_COMMENT", "块注释未闭合，期望 */", start)
        # 最长匹配：优先识别 >=，而不是先把 > 当成一个完整符号。
        symbol = pair if pair in SYMBOLS else self.peek()
        if symbol in SYMBOLS:
            for _ in symbol:
                self.advance()
            # 冻结 TokenKind 没有别名种别；兼容词素在 value 中规范化，Parser
            # 通过同一 EQ/NE 种别即可保持语义一致，同时 lexeme 保留原文。
            value = {"==": "=", "<>": "!="}.get(symbol, symbol)
            return self._token(SYMBOLS[symbol], start, value)
        char = self.advance()
        raise self._error("ILLEGAL_CHARACTER", f"非法字符 {char!r}", start)

    def tokenize(self) -> list[Token]:
        """返回当前位置到输入结束的 Token，末尾恰好一个零宽 EOF。"""
        tokens = []
        while self.peek():
            char = self.peek()
            # 根据首字符选择扫描函数；空白和注释只推进游标，不进入结果列表。
            if char.isspace():
                self.advance()
            elif _name_start(char):
                tokens.append(self.scan_identifier())
            elif _digit(char):
                tokens.append(self.scan_number())
            elif char == "'":
                tokens.append(self.scan_string())
            else:
                token = self.scan_operator_or_comment()
                if token is not None:
                    tokens.append(token)
        # EOF 告诉解析器输入已经结束，它不占用源码中的实际字符。
        tokens.append(self._token(K.EOF, self.position(), None))
        return tokens
