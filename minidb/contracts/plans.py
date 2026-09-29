"""编译器与执行器之间的逻辑计划契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from .bound import BoundExpr
from .extensions import ExtensionPlan
from .metadata import ColumnMeta, StoredValue, TableMeta, normalize_identifier
from .source import Span


@dataclass(frozen=True, slots=True)
class CreateTablePlan:
    name: str
    columns: tuple[ColumnMeta, ...]
    span: Span

    def __post_init__(self) -> None:
        if self.name != normalize_identifier(self.name):
            raise ValueError("CreateTablePlan.name 必须使用小写规范名")


@dataclass(frozen=True, slots=True)
class InsertPlan:
    table: TableMeta
    values: tuple[StoredValue, ...]
    span: Span

    def __post_init__(self) -> None:
        if len(self.values) != len(self.table.columns):
            raise ValueError("InsertPlan.values 宽度必须等于表列数")


@dataclass(frozen=True, slots=True)
class SeqScanPlan:
    table: TableMeta
    span: Span


@dataclass(frozen=True, slots=True)
class FilterPlan:
    child: PlanNode
    predicate: BoundExpr
    span: Span


@dataclass(frozen=True, slots=True)
class ProjectPlan:
    child: PlanNode
    indices: tuple[int, ...]
    names: tuple[str, ...]
    span: Span

    def __post_init__(self) -> None:
        if len(self.indices) != len(self.names):
            raise ValueError("ProjectPlan.indices 与 names 必须等长")
        if any(index < 0 for index in self.indices):
            raise ValueError("ProjectPlan.indices 不能包含负数")


@dataclass(frozen=True, slots=True)
class DeletePlan:
    table: TableMeta
    child: PlanNode
    span: Span

    def __post_init__(self) -> None:
        node: PlanNode = self.child
        while isinstance(node, FilterPlan):
            node = node.child
        if not isinstance(node, SeqScanPlan):
            raise TypeError("DeletePlan.child 必须是 SeqScanPlan 或其上的 FilterPlan")
        if node.table != self.table:
            raise ValueError("DeletePlan.child 必须扫描同一张表")


CorePlanNode: TypeAlias = (
    CreateTablePlan | InsertPlan | SeqScanPlan | FilterPlan | ProjectPlan | DeletePlan
)
PlanNode: TypeAlias = CorePlanNode | ExtensionPlan

__all__ = [
    "CorePlanNode",
    "CreateTablePlan",
    "DeletePlan",
    "FilterPlan",
    "InsertPlan",
    "PlanNode",
    "ProjectPlan",
    "SeqScanPlan",
]
