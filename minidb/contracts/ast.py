"""不可变、保留原始语法顺序的核心 AST；不在此进行语义检查。"""

from dataclasses import dataclass
from typing import TypeAlias

from .source import Span


@dataclass(frozen=True)
class Literal:
    value: int | float | str
    span: Span


@dataclass(frozen=True)
class Identifier:
    name: str
    span: Span


@dataclass(frozen=True)
class UnaryExpr:
    op: str
    operand: "Expr"
    span: Span


@dataclass(frozen=True)
class BinaryExpr:
    op: str
    left: "Expr"
    right: "Expr"
    span: Span


Expr: TypeAlias = Literal | Identifier | UnaryExpr | BinaryExpr


@dataclass(frozen=True)
class ColumnDef:
    name: Identifier
    dtype_name: str
    span: Span


@dataclass(frozen=True)
class CreateTableStmt:
    name: Identifier
    columns: tuple[ColumnDef, ...]
    span: Span


@dataclass(frozen=True)
class InsertStmt:
    table: Identifier
    columns: tuple[Identifier, ...] | None
    values: tuple[Literal, ...]
    span: Span


@dataclass(frozen=True)
class SelectStmt:
    table: Identifier
    columns: tuple[Identifier, ...] | None
    where: Expr | None
    span: Span


@dataclass(frozen=True)
class DeleteStmt:
    table: Identifier
    where: Expr | None
    span: Span


Statement: TypeAlias = CreateTableStmt | InsertStmt | SelectStmt | DeleteStmt
