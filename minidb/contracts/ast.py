"""未经名称绑定的 SQL 抽象语法树契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from .extensions import ExtensionStatement
from .metadata import normalize_identifier
from .source import Span


@dataclass(frozen=True, slots=True)
class Literal:
    value: int | float | str
    span: Span

    def __post_init__(self) -> None:
        if isinstance(self.value, bool):
            raise TypeError("核心 Literal 不接受 bool")


@dataclass(frozen=True, slots=True)
class Identifier:
    name: str
    span: Span

    def __post_init__(self) -> None:
        if self.name != normalize_identifier(self.name):
            raise ValueError("Identifier.name 必须是小写规范标识符")


@dataclass(frozen=True, slots=True)
class UnaryExpr:
    op: str
    operand: Expr
    span: Span


@dataclass(frozen=True, slots=True)
class BinaryExpr:
    op: str
    left: Expr
    right: Expr
    span: Span


Expr: TypeAlias = Literal | Identifier | UnaryExpr | BinaryExpr


@dataclass(frozen=True, slots=True)
class ColumnDef:
    name: Identifier
    dtype_name: str
    span: Span

    def __post_init__(self) -> None:
        if self.dtype_name not in {"INT", "VARCHAR"}:
            raise ValueError("核心列类型必须是 INT 或 VARCHAR")


@dataclass(frozen=True, slots=True)
class CreateTableStmt:
    name: Identifier
    columns: tuple[ColumnDef, ...]
    span: Span

    def __post_init__(self) -> None:
        if not self.columns:
            raise ValueError("CreateTableStmt.columns 不能为空")


@dataclass(frozen=True, slots=True)
class InsertStmt:
    table: Identifier
    columns: tuple[Identifier, ...] | None
    values: tuple[Literal, ...]
    span: Span

    def __post_init__(self) -> None:
        if self.columns is not None and not self.columns:
            raise ValueError("显式 INSERT 列表不能为空")
        if not self.values:
            raise ValueError("InsertStmt.values 不能为空")


@dataclass(frozen=True, slots=True)
class SelectStmt:
    table: Identifier
    columns: tuple[Identifier, ...] | None
    where: Expr | None
    span: Span


@dataclass(frozen=True, slots=True)
class DeleteStmt:
    table: Identifier
    where: Expr | None
    span: Span


CoreStatement: TypeAlias = CreateTableStmt | InsertStmt | SelectStmt | DeleteStmt
Statement: TypeAlias = CoreStatement | ExtensionStatement

__all__ = [
    "BinaryExpr",
    "ColumnDef",
    "CoreStatement",
    "CreateTableStmt",
    "DeleteStmt",
    "Expr",
    "Identifier",
    "InsertStmt",
    "Literal",
    "SelectStmt",
    "Statement",
    "UnaryExpr",
]
