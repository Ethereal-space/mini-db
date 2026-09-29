"""词法单元契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, TypeAlias

from .source import Span


class TokenKind(Enum):
    """核心 SQL 与预留扩展使用的 Token 种别。"""

    EOF = auto()
    IDENTIFIER = auto()
    INTEGER = auto()
    FLOAT = auto()
    STRING = auto()

    CREATE = auto()
    TABLE = auto()
    INSERT = auto()
    INTO = auto()
    VALUES = auto()
    SELECT = auto()
    FROM = auto()
    WHERE = auto()
    DELETE = auto()
    INT = auto()
    VARCHAR = auto()
    AND = auto()
    OR = auto()
    NOT = auto()

    UPDATE = auto()
    SET = auto()
    ORDER = auto()
    BY = auto()
    ASC = auto()
    DESC = auto()
    LIMIT = auto()
    DISTINCT = auto()
    JOIN = auto()
    INNER = auto()
    LEFT = auto()
    RIGHT = auto()
    FULL = auto()
    ON = auto()
    AS = auto()
    GROUP = auto()
    HAVING = auto()
    NULL = auto()
    TRUE = auto()
    FALSE = auto()
    BOOL = auto()
    EXPLAIN = auto()
    BEGIN = auto()
    COMMIT = auto()
    ROLLBACK = auto()

    LEFT_PAREN = auto()
    RIGHT_PAREN = auto()
    COMMA = auto()
    SEMICOLON = auto()
    DOT = auto()
    STAR = auto()
    PLUS = auto()
    MINUS = auto()
    SLASH = auto()
    PERCENT = auto()
    EQ = auto()
    NE = auto()
    LT = auto()
    LE = auto()
    GT = auto()
    GE = auto()


TokenValue: TypeAlias = int | float | str | None


@dataclass(frozen=True, slots=True)
class Token:
    """Lexer 的稳定输出。

    ``lexeme`` 永远保留原文，``value`` 保存解码后的字面量或规范化标识符。
    EOF 的 ``value`` 为 ``None``，其 ``span`` 为零宽区间。
    """

    kind: TokenKind
    lexeme: str
    value: TokenValue
    span: Span


CORE_KEYWORDS: Final[Mapping[str, TokenKind]] = MappingProxyType({
    "create": TokenKind.CREATE,
    "table": TokenKind.TABLE,
    "insert": TokenKind.INSERT,
    "into": TokenKind.INTO,
    "values": TokenKind.VALUES,
    "select": TokenKind.SELECT,
    "from": TokenKind.FROM,
    "where": TokenKind.WHERE,
    "delete": TokenKind.DELETE,
    "int": TokenKind.INT,
    "varchar": TokenKind.VARCHAR,
    "and": TokenKind.AND,
    "or": TokenKind.OR,
    "not": TokenKind.NOT,
})

EXTENSION_KEYWORDS: Final[Mapping[str, TokenKind]] = MappingProxyType({
    "update": TokenKind.UPDATE,
    "set": TokenKind.SET,
    "order": TokenKind.ORDER,
    "by": TokenKind.BY,
    "asc": TokenKind.ASC,
    "desc": TokenKind.DESC,
    "limit": TokenKind.LIMIT,
    "distinct": TokenKind.DISTINCT,
    "join": TokenKind.JOIN,
    "inner": TokenKind.INNER,
    "left": TokenKind.LEFT,
    "right": TokenKind.RIGHT,
    "full": TokenKind.FULL,
    "on": TokenKind.ON,
    "as": TokenKind.AS,
    "group": TokenKind.GROUP,
    "having": TokenKind.HAVING,
    "null": TokenKind.NULL,
    "true": TokenKind.TRUE,
    "false": TokenKind.FALSE,
    "bool": TokenKind.BOOL,
    "explain": TokenKind.EXPLAIN,
    "begin": TokenKind.BEGIN,
    "commit": TokenKind.COMMIT,
    "rollback": TokenKind.ROLLBACK,
})

ALL_KEYWORDS: Final[Mapping[str, TokenKind]] = MappingProxyType(
    {**CORE_KEYWORDS, **EXTENSION_KEYWORDS}
)

__all__ = [
    "ALL_KEYWORDS",
    "CORE_KEYWORDS",
    "EXTENSION_KEYWORDS",
    "Token",
    "TokenKind",
    "TokenValue",
]
