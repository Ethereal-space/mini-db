"""成员模块之间冻结的最小运行接口。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from .ast import Statement
from .metadata import ColumnMeta, StoredValue, TableMeta
from .plans import PlanNode
from .results import BufferStats, ExecutionResult, RID, StoredRow


@runtime_checkable
class FrontendPort(Protocol):
    def parse(self, source: str) -> list[Statement]: ...


@runtime_checkable
class CatalogPort(Protocol):
    def table_exists(self, name: str) -> bool: ...

    def get_table(self, name: str) -> TableMeta: ...

    def allocate_table_id(self) -> int: ...

    def register_table(self, table: TableMeta) -> None: ...

    def list_tables(self) -> list[TableMeta]: ...


@runtime_checkable
class CatalogRepositoryPort(Protocol):
    def load_tables(self) -> list[TableMeta]: ...

    def save_table(self, table: TableMeta) -> None: ...


@runtime_checkable
class CompilerPort(Protocol):
    def compile(self, statement: Statement, catalog: CatalogPort) -> PlanNode: ...

    def optimize(self, plan: PlanNode) -> PlanNode: ...


@runtime_checkable
class StoragePort(Protocol):
    def create_table(self, table_id: int, columns: tuple[ColumnMeta, ...]) -> int: ...

    def insert(self, table: TableMeta, values: tuple[StoredValue, ...]) -> RID: ...

    def scan(self, table: TableMeta) -> Iterator[StoredRow]: ...

    def mark_delete(self, table: TableMeta, rid: RID) -> None: ...

    def flush_all(self) -> None: ...

    def stats(self) -> BufferStats: ...


@runtime_checkable
class ExecutorPort(Protocol):
    def execute(self, plan: PlanNode) -> ExecutionResult: ...


@runtime_checkable
class ApplicationPort(Protocol):
    def execute(self, source: str, trace: bool) -> list[ExecutionResult]: ...


__all__ = [
    "ApplicationPort",
    "CatalogPort",
    "CatalogRepositoryPort",
    "CompilerPort",
    "ExecutorPort",
    "FrontendPort",
    "StoragePort",
]
