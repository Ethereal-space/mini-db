from __future__ import annotations

import pytest

from minidb.contracts import (
    BoundBinary,
    BoundColumn,
    BoundLiteral,
    BufferStats,
    ColumnMeta,
    DataType,
    DeletePlan,
    FilterPlan,
    RID,
    SeqScanPlan,
    StorageError,
    StoredRow,
    TableMeta,
    UNKNOWN_SPAN,
)
from minidb.runtime.command_executors import execute_delete, preview_delete


COLUMNS = (
    ColumnMeta("id", DataType.INT, 0),
    ColumnMeta("name", DataType.VARCHAR, 1),
    ColumnMeta("age", DataType.INT, 2),
)
TABLE = TableMeta(1, "student", COLUMNS, 2)


class FakeStorage:
    def __init__(self, rows: list[StoredRow], *, fail_at: int | None = None) -> None:
        self.rows = rows
        self.fail_at = fail_at
        self.deleted: set[RID] = set()
        self.marked: list[RID] = []
        self.flush_count = 0
        self.close_count = 0

    def scan(self, table: TableMeta):
        assert table == TABLE
        try:
            for row in tuple(self.rows):
                if row.rid not in self.deleted:
                    yield row
        finally:
            self.close_count += 1

    def mark_delete(self, table: TableMeta, rid: RID) -> None:
        assert table == TABLE
        if self.fail_at is not None and len(self.marked) + 1 == self.fail_at:
            raise StorageError(
                "DELETE_FAILED",
                "模拟删除失败",
                context={"page_id": rid.page_id, "slot_id": rid.slot_id},
            )
        self.marked.append(rid)
        self.deleted.add(rid)

    def flush_all(self) -> None:
        self.flush_count += 1

    def stats(self) -> BufferStats:
        return BufferStats()


def _age_filter(minimum: int) -> FilterPlan:
    predicate = BoundBinary(
        ">=",
        BoundColumn(2, "age", DataType.INT, UNKNOWN_SPAN),
        BoundLiteral(minimum, DataType.INT, UNKNOWN_SPAN),
        DataType.BOOL,
        UNKNOWN_SPAN,
    )
    return FilterPlan(SeqScanPlan(TABLE, UNKNOWN_SPAN), predicate, UNKNOWN_SPAN)


def test_db04_partial() -> None:
    storage = FakeStorage(
        [
            StoredRow(RID(2, 0), (1, "Minor", 17)),
            StoredRow(RID(2, 1), (2, "Alice", 20)),
            StoredRow(RID(3, 0), (3, "张三", 22)),
        ]
    )
    plan = DeletePlan(TABLE, _age_filter(18), UNKNOWN_SPAN)

    result = execute_delete(plan, storage)

    assert storage.marked == [RID(2, 1), RID(3, 0)]
    assert result.affected_rows == 2
    assert storage.flush_count == 1


def test_db04_all() -> None:
    rows = [
        StoredRow(RID(2, 0), (1, "A", 18)),
        StoredRow(RID(2, 1), (2, "B", 19)),
        StoredRow(RID(3, 0), (3, "C", 20)),
    ]
    storage = FakeStorage(rows)
    plan = DeletePlan(TABLE, SeqScanPlan(TABLE, UNKNOWN_SPAN), UNKNOWN_SPAN)

    result = execute_delete(plan, storage)

    assert storage.marked == [row.rid for row in rows]
    assert result.affected_rows == 3
    assert storage.flush_count == 1
    assert list(storage.scan(TABLE)) == []


def test_db04_none() -> None:
    storage = FakeStorage([StoredRow(RID(2, 0), (1, "Minor", 17))])
    plan = DeletePlan(TABLE, _age_filter(18), UNKNOWN_SPAN)

    result = execute_delete(plan, storage)

    assert result.affected_rows == 0
    assert storage.marked == []
    assert storage.flush_count == 1


def test_db04_equal_values() -> None:
    rows = [
        StoredRow(RID(2, 0), (1, "Same", 20)),
        StoredRow(RID(3, 4), (1, "Same", 20)),
    ]
    storage = FakeStorage(rows)
    plan = DeletePlan(TABLE, SeqScanPlan(TABLE, UNKNOWN_SPAN), UNKNOWN_SPAN)

    result = execute_delete(plan, storage)

    assert storage.marked == [RID(2, 0), RID(3, 4)]
    assert result.affected_rows == 2


def test_db04_failure() -> None:
    rows = [
        StoredRow(RID(2, 0), (1, "A", 18)),
        StoredRow(RID(2, 1), (2, "B", 19)),
    ]
    storage = FakeStorage(rows, fail_at=2)
    plan = DeletePlan(TABLE, SeqScanPlan(TABLE, UNKNOWN_SPAN), UNKNOWN_SPAN)

    with pytest.raises(StorageError) as captured:
        execute_delete(plan, storage)

    assert captured.value.stage == "STORAGE"
    assert storage.marked == [RID(2, 0)]
    assert storage.close_count == 1
    assert storage.flush_count == 0


def test_db04_preview_is_read_only() -> None:
    rows = [StoredRow(RID(2, 0), (1, "Alice", 20))]
    storage = FakeStorage(rows)
    plan = DeletePlan(TABLE, SeqScanPlan(TABLE, UNKNOWN_SPAN), UNKNOWN_SPAN)

    assert preview_delete(plan, storage) == tuple(rows)
    assert storage.marked == []
    assert storage.flush_count == 0
