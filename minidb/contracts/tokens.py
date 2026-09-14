"""Token 的 kind 是语法种别，lexeme 保留原文，value 保存规范值。"""

from dataclasses import dataclass
from enum import StrEnum

from .source import Span


class TokenKind(StrEnum):
    SELECT = "SELECT"
    FROM = "FROM"
    WHERE = "WHERE"
    CREATE = "CREATE"
    TABLE = "TABLE"
    INSERT = "INSERT"
    INTO = "INTO"
    VALUES = "VALUES"
    DELETE = "DELETE"
    INT = "INT"
    VARCHAR = "VARCHAR"
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    IDENTIFIER = "IDENTIFIER"
    UNSUPPORTED_KEYWORD = "UNSUPPORTED_KEYWORD"
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    STRING = "STRING"
    EQ = "EQ"
    NE = "NE"
    LT = "LT"
    LE = "LE"
    GT = "GT"
    GE = "GE"
    EQ_ALIAS = "EQ_ALIAS"
    NE_ALIAS = "NE_ALIAS"
    PLUS = "PLUS"
    MINUS = "MINUS"
    STAR = "STAR"
    SLASH = "SLASH"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    COMMA = "COMMA"
    SEMICOLON = "SEMICOLON"
    EOF = "EOF"


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    lexeme: str
    value: object
    span: Span
