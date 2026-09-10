"""通过 Catalog 名称绑定与类型检查后的语句契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from .extensions import BoundExtension
from .metadata import ColumnMeta, DataType, ScalarValue, StoredValue, TableMeta, normalize_identifier
from .source import Span


def _value_matches_type(value: ScalarValue, dtype: DataType) -> bool:
    if dtype is DataType.BOOL:
        return isinstance(value, bool)
    if dtype is DataType.INT:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, str)


@dataclass(frozen=True, slots=True)
class BoundLiteral:
    value: ScalarValue
    dtype: DataType
    span: Span

    def __post_init__(self) -> None:
        if not _value_matches_type(self.value, self.dtype):
            raise TypeError(f"值 {self.value!r} 与 {self.dtype.value} 不匹配")


@dataclass(frozen=True, slots=True)
class BoundColumn:
    index: int
    name: str
    dtype: DataType
    span: Span

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("BoundColumn.index 必须大于或等于 0")
        if self.name != normalize_identifier(self.name):
            raise ValueError("BoundColumn.name 必须使用小写规范名")


@dataclass(frozen=True, slots=True)
class BoundUnary:
    op: str
    operand: BoundExpr
    dtype: DataType
    span: Span


@dataclass(frozen=True, slots=True)
class BoundBinary:
    op: str
    left: BoundExpr
    right: BoundExpr
    dtype: DataType
    span: Span


BoundExpr: TypeAlias = BoundLiteral | BoundColumn | BoundUnary | BoundBinary


@dataclass(frozen=True, slots=True)
class BoundCreate:
    name: str
    columns: tuple[ColumnMeta, ...]
    span: Span

    def __post_init__(self) -> None:
        if self.name != normalize_identifier(self.name):
            raise ValueError("BoundCreate.name 必须使用小写规范名")
        if not self.columns:
            raise ValueError("BoundCreate.columns 不能为空")


@dataclass(frozen=True, slots=True)
class BoundInsert:
    table: TableMeta
    values: tuple[StoredValue, ...]
    span: Span

    def __post_init__(self) -> None:
        if len(self.values) != len(self.table.columns):
            raise ValueError("BoundInsert.values 宽度必须等于表列数")
        for value, column in zip(self.values, self.table.columns, strict=True):
            if not _value_matches_type(value, column.dtype):
                raise TypeError(f"列 {column.name} 的值与 {column.dtype.value} 不匹配")


@dataclass(frozen=True, slots=True)
class BoundSelect:
    table: TableMeta
    indices: tuple[int, ...]
    names: tuple[str, ...]
    where: BoundExpr | None
    span: Span

    def __post_init__(self) -> None:
        if len(self.indices) != len(self.names):
            raise ValueError("BoundSelect.indices 与 names 必须等长")
        if any(index < 0 for index in self.indices):
            raise ValueError("BoundSelect.indices 不能包含负数")


@dataclass(frozen=True, slots=True)
class BoundDelete:
    table: TableMeta
    where: BoundExpr | None
    span: Span


CoreBoundStatement: TypeAlias = BoundCreate | BoundInsert | BoundSelect | BoundDelete
BoundStatement: TypeAlias = CoreBoundStatement | BoundExtension

__all__ = [
    "BoundBinary",
    "BoundColumn",
    "BoundCreate",
    "BoundDelete",
    "BoundExpr",
    "BoundInsert",
    "BoundLiteral",
    "BoundSelect",
    "BoundStatement",
    "BoundUnary",
    "CoreBoundStatement",
]
