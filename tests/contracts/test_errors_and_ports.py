from __future__ import annotations

from collections.abc import Iterator

import pytest

from minidb.contracts import (
    ApplicationPort,
    BufferStats,
    CatalogPort,
    CatalogRepositoryPort,
    ColumnMeta,
    CompilerPort,
    DataType,
    ExecutionResult,
    ExecutorPort,
    FrontendPort,
    LexicalError,
    MiniDBError,
    Position,
    RID,
    Span,
    StoragePort,
    StoredRow,
    TableMeta,
)


class CompleteAdapter:
    def parse(self, source: str) -> list[object]:
        return []

    def table_exists(self, name: str) -> bool:
        return False

    def get_table(self, name: str) -> TableMeta:
        return TableMeta(1, "t", (ColumnMeta("id", DataType.INT, 0),), 2)

    def allocate_table_id(self) -> int:
        return 1

    def register_table(self, table: TableMeta) -> None:
        self.last_table = table

    def list_tables(self) -> list[TableMeta]:
        return []

    def load_tables(self) -> list[TableMeta]:
        return []

    def save_table(self, table: TableMeta) -> None:
        self.last_table = table

    def compile(self, statement: object, catalog: CatalogPort) -> object:
        return statement

    def optimize(self, plan: object) -> object:
        return plan

    def create_table(self, table_id: int, columns: tuple[ColumnMeta, ...]) -> int:
        return 2

    def insert(self, table: TableMeta, values: tuple[int | str, ...]) -> RID:
        return RID(2, 0)

    def scan(self, table: TableMeta) -> Iterator[StoredRow]:
        return iter(())

    def mark_delete(self, table: TableMeta, rid: RID) -> None:
        self.last_rid = rid

    def flush_all(self) -> None:
        self.flushed = True

    def stats(self) -> BufferStats:
        return BufferStats()

    def execute(self, value: object, trace: bool = False) -> ExecutionResult | list[ExecutionResult]:
        if isinstance(value, str):
            return []
        return ExecutionResult()


def test_stage_error_has_stable_fields_and_immutable_context() -> None:
    span = Span(Position(7, 1, 8), Position(8, 1, 9))
    error = LexicalError("illegal_character", "非法字符 @", span, {"character": "@"})
    assert error.stage == "LEXICAL"
    assert error.code == "ILLEGAL_CHARACTER"
    assert "第1行第8列" in str(error)
    with pytest.raises(TypeError):
        error.context["character"] = "#"  # type: ignore[index]
    with pytest.raises(ValueError):
        MiniDBError("UNKNOWN", "X", "message")


def test_runtime_checkable_ports_are_structural() -> None:
    adapter = CompleteAdapter()
    assert isinstance(adapter, FrontendPort)
    assert isinstance(adapter, CatalogPort)
    assert isinstance(adapter, CatalogRepositoryPort)
    assert isinstance(adapter, CompilerPort)
    assert isinstance(adapter, StoragePort)
    assert isinstance(adapter, ExecutorPort)
    assert isinstance(adapter, ApplicationPort)
