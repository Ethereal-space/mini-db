from __future__ import annotations

import json

import pytest

from minidb.contracts import (
    BufferStats,
    ColumnMeta,
    DataType,
    ExecutionResult,
    ExtensionPlan,
    InsertPlan,
    ProjectPlan,
    RID,
    SeqScanPlan,
    StoredRow,
    TableMeta,
    TraceEvent,
    UNKNOWN_SPAN,
    UnsupportedError,
)
from minidb.runtime.execution_service import ExecutionService
from minidb.runtime.result_formatter import format_result


COLUMNS = (
    ColumnMeta("id", DataType.INT, 0),
    ColumnMeta("name", DataType.VARCHAR, 1),
    ColumnMeta("age", DataType.INT, 2),
)
TABLE = TableMeta(1, "student", COLUMNS, 2)


class FakeCatalog:
    def table_exists(self, name: str) -> bool:
        return False

    def allocate_table_id(self) -> int:
        return 2

    def register_table(self, table: TableMeta) -> None:
        self.registered = table

    def get_table(self, name: str) -> TableMeta:
        return TABLE

    def list_tables(self) -> list[TableMeta]:
        return [TABLE]


class FakeStorage:
    def __init__(self, rows: list[StoredRow]) -> None:
        self.rows = rows
        self.inserted: list[tuple[TableMeta, tuple[int | str, ...]]] = []
        self.flush_count = 0

    def create_table(self, table_id: int, columns: tuple[ColumnMeta, ...]) -> int:
        return 4

    def insert(self, table: TableMeta, values: tuple[int | str, ...]) -> RID:
        self.inserted.append((table, values))
        return RID(2, 9)

    def scan(self, table: TableMeta):
        yield from tuple(self.rows)

    def mark_delete(self, table: TableMeta, rid: RID) -> None:
        self.rows = [row for row in self.rows if row.rid != rid]

    def flush_all(self) -> None:
        self.flush_count += 1

    def stats(self) -> BufferStats:
        return BufferStats(hits=1, misses=2, reads=2)


def _project_plan() -> ProjectPlan:
    return ProjectPlan(
        SeqScanPlan(TABLE, UNKNOWN_SPAN),
        (0, 1),
        ("id", "name"),
        UNKNOWN_SPAN,
    )


def test_db05_select() -> None:
    storage = FakeStorage(
        [
            StoredRow(RID(2, 0), (1, "Alice", 20)),
            StoredRow(RID(2, 1), (2, "张三", 21)),
        ]
    )
    service = ExecutionService(FakeCatalog(), storage)

    result = service.execute(_project_plan())

    assert result.columns == ("id", "name")
    assert result.rows == ((1, "Alice"), (2, "张三"))
    assert all(len(row) == len(result.columns) for row in result.rows)


def test_db05_command() -> None:
    storage = FakeStorage([])
    service = ExecutionService(FakeCatalog(), storage)
    plan = InsertPlan(TABLE, (4, "李四", 21), UNKNOWN_SPAN)

    result = service.execute(plan)

    assert result.columns == ()
    assert result.rows == ()
    assert result.affected_rows == 1
    assert storage.inserted == [(TABLE, (4, "李四", 21))]
    assert storage.flush_count == 1


def test_db05_trace() -> None:
    sink: list[TraceEvent] = []
    storage = FakeStorage([StoredRow(RID(2, 0), (1, "Alice", 20))])
    service = ExecutionService(FakeCatalog(), storage, sink.append)

    first = service.execute(_project_plan())
    storage.rows = [StoredRow(RID(3, 1), (2, "Bob", 19))]
    second = service.execute(_project_plan())

    first_details = [event.detail for event in first.trace]
    second_details = [event.detail for event in second.trace]
    assert any("rid=2:0" in detail for detail in first_details)
    assert not any("rid=2:0" in detail for detail in second_details)
    assert any("rid=3:1" in detail for detail in second_details)
    assert first.trace is not second.trace
    assert tuple(sink) == first.trace + second.trace


def test_db05_unknown() -> None:
    service = ExecutionService(FakeCatalog(), FakeStorage([]))
    plan = ExtensionPlan("mystery", 1, {}, UNKNOWN_SPAN)

    with pytest.raises(UnsupportedError) as captured:
        service.execute(plan)

    assert captured.value.stage == "UNSUPPORTED"
    assert captured.value.code == "UNSUPPORTED_FEATURE"
    assert captured.value.context == {"feature": "mystery", "version": 1}


def test_db05_format() -> None:
    empty = ExecutionResult(columns=("name",), rows=())
    rich = ExecutionResult(
        columns=("name",),
        rows=(("张三\nA|B",),),
        message="中文结果",
        trace=(TraceEvent("EXECUTION", "ROWS count=1"),),
    )
    snapshot = rich

    empty_text = format_result(empty)
    rich_text = format_result(rich)

    assert "name" in empty_text
    assert "返回 0 行" in empty_text
    assert "张三\\nA\\|B" in rich_text
    assert "中文结果" in rich_text
    assert rich == snapshot


def test_db05_json_format_round_trip() -> None:
    result = ExecutionResult(
        columns=("name",),
        rows=(("张三",),),
        trace=(TraceEvent("EXECUTION", "完成"),),
    )

    decoded = json.loads(format_result(result, "json"))

    assert decoded["columns"] == ["name"]
    assert decoded["rows"] == [["张三"]]
    assert decoded["trace"] == [{"stage": "EXECUTION", "detail": "完成"}]
