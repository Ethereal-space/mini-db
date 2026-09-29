"""Catalog 元数据与核心值类型契约。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final, TypeAlias


class DataType(str, Enum):
    """核心类型集合；BOOL 仅用于表达式中间结果。"""

    INT = "INT"
    VARCHAR = "VARCHAR"
    BOOL = "BOOL"


StoredValue: TypeAlias = int | str
ScalarValue: TypeAlias = int | str | bool

MAX_IDENTIFIER_LENGTH: Final = 64
_IDENTIFIER_PATTERN: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def normalize_identifier(name: str) -> str:
    """校验 ASCII 标识符并返回不区分大小写的规范名。"""

    if not _IDENTIFIER_PATTERN.fullmatch(name):
        raise ValueError(f"非法标识符 {name!r}")
    if len(name) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"标识符长度不能超过 {MAX_IDENTIFIER_LENGTH}")
    return name.casefold()


@dataclass(frozen=True, slots=True)
class ColumnMeta:
    name: str
    dtype: DataType
    ordinal: int

    def __post_init__(self) -> None:
        if self.name != normalize_identifier(self.name):
            raise ValueError("ColumnMeta.name 必须使用小写规范名")
        if self.dtype is DataType.BOOL:
            raise ValueError("BOOL 仅用于表达式中间结果，不能作为核心存储列类型")
        if self.ordinal < 0:
            raise ValueError("ColumnMeta.ordinal 必须大于或等于 0")


@dataclass(frozen=True, slots=True)
class TableMeta:
    table_id: int
    name: str
    columns: tuple[ColumnMeta, ...]
    first_page_id: int

    def __post_init__(self) -> None:
        if self.table_id < 1:
            raise ValueError("TableMeta.table_id 必须大于或等于 1")
        if self.name != normalize_identifier(self.name):
            raise ValueError("TableMeta.name 必须使用小写规范名")
        if self.first_page_id < 1:
            raise ValueError("TableMeta.first_page_id 必须大于或等于 1")
        if not self.columns:
            raise ValueError("TableMeta.columns 不能为空")
        if tuple(column.ordinal for column in self.columns) != tuple(range(len(self.columns))):
            raise ValueError("TableMeta.columns 的 ordinal 必须从 0 连续递增")
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("TableMeta.columns 不能包含重复列名")


__all__ = [
    "ColumnMeta",
    "DataType",
    "MAX_IDENTIFIER_LENGTH",
    "ScalarValue",
    "StoredValue",
    "TableMeta",
    "normalize_identifier",
]
