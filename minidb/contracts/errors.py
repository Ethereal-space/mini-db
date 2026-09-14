"""领域错误保留机器可读阶段、错误码、源码位置和诊断上下文。"""

from .source import Span


class MiniDBError(Exception):
    def __init__(self, stage: str, code: str, message: str,
                 span: Span | None = None, context: dict | None = None):
        self.stage = stage
        self.code = code
        self.message = message
        self.span = span
        self.context = dict(context or {})
        super().__init__(message)

    def __str__(self) -> str:
        location = ""
        if self.span is not None:
            location = f" at line {self.span.start.line}, column {self.span.start.column}"
        return f"{self.stage}[{self.code}]{location}: {self.message}"


class LexicalError(MiniDBError):
    def __init__(self, code: str, message: str, span: Span, context: dict | None = None):
        super().__init__("LEXICAL", code, message, span, context)


class SyntaxError(MiniDBError):
    """MiniDB 语法错误，与 Python 内置 SyntaxError 不同。"""

    def __init__(self, code: str, message: str, span: Span, context: dict | None = None):
        super().__init__("SYNTAX", code, message, span, context)
