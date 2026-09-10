"""跨模块使用的结构化错误契约。"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from .source import Span


ERROR_STAGES: Final[frozenset[str]] = frozenset(
    {
        "LEXICAL",
        "SYNTAX",
        "SEMANTIC",
        "PLANNING",
        "OPTIMIZATION",
        "EXECUTION",
        "STORAGE",
        "IO",
        "UNSUPPORTED",
    }
)


class MiniDBError(Exception):
    """具有稳定阶段、代码、消息、位置与上下文的领域错误。"""

    def __init__(
        self,
        stage: str,
        code: str,
        message: str,
        span: Span | None = None,
        context: Mapping[str, object] | None = None,
    ) -> None:
        normalized_stage = stage.upper()
        if normalized_stage not in ERROR_STAGES:
            raise ValueError(f"未知错误阶段 {stage!r}")
        normalized_code = code.upper()
        if not normalized_code or any(character.isspace() for character in normalized_code):
            raise ValueError("错误代码必须是非空且不含空白的标识")
        if not message:
            raise ValueError("错误消息不能为空")
        self.stage = normalized_stage
        self.code = normalized_code
        self.message = message
        self.span = span
        self.context = MappingProxyType(dict(context or {}))
        super().__init__(self.__str__())

    def __str__(self) -> str:
        location = ""
        if self.span is not None:
            location = f" 第{self.span.start.line}行第{self.span.start.column}列"
        return f"[{self.stage}/{self.code}]{location} {self.message}"


class _StageError(MiniDBError):
    STAGE: str

    def __init__(
        self,
        code: str,
        message: str,
        span: Span | None = None,
        context: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(self.STAGE, code, message, span, context)


class LexicalError(_StageError):
    STAGE = "LEXICAL"


class SyntaxError(_StageError):
    """MiniDB 语法错误；有意与内置异常同名以匹配课程契约。"""

    STAGE = "SYNTAX"


class SemanticError(_StageError):
    STAGE = "SEMANTIC"


class PlanningError(_StageError):
    STAGE = "PLANNING"


class OptimizationError(_StageError):
    STAGE = "OPTIMIZATION"


class ExecutionError(_StageError):
    STAGE = "EXECUTION"


class StorageError(_StageError):
    STAGE = "STORAGE"


class StorageIOError(_StageError):
    STAGE = "IO"


class UnsupportedError(_StageError):
    STAGE = "UNSUPPORTED"


__all__ = [
    "ERROR_STAGES",
    "ExecutionError",
    "LexicalError",
    "MiniDBError",
    "OptimizationError",
    "PlanningError",
    "SemanticError",
    "StorageError",
    "StorageIOError",
    "SyntaxError",
    "UnsupportedError",
]
