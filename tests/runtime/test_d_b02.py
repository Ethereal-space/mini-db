from __future__ import annotations

import pytest

from minidb.contracts import (
    BoundBinary,
    BoundColumn,
    BoundLiteral,
    ColumnMeta,
    DataType,
    ExecutionError,
    FilterPlan,
    ProjectPlan,
    RID,
    SeqScanPlan,
    StoredRow,
    TableMeta,
    UNKNOWN_SPAN,
)
from minidb.runtime.operators import build_operator


COLUMNS = (
    ColumnMeta("id", DataType.INT, 0),
    ColumnMeta("name", DataType.VARCHAR, 1),
    ColumnMeta("age", DataType.INT, 2),
)
TABLE = TableMeta(1, "student", COLUMNS, 2)


class FakeStorage:
    def __init__(self, rows: list[StoredRow]) -> None:
        self.rows = rows
        self.scan_calls = 0
        self.yield_count = 0
        self.close_count = 0

    def scan(self, table: TableMeta):
        assert table == TABLE
        self.scan_calls += 1
        try:
            for row in self.rows:
                self.yield_count += 1
                yield row
        finally:
            self.close_count += 1


def test_db02_empty() -> None:
    storage = FakeStorage([])
    operator = build_operator(SeqScanPlan(TABLE, UNKNOWN_SPAN), storage)

    operator.open()
    assert operator.next() is None
    assert operator.next() is None

    assert storage.scan_calls == 1
    assert storage.close_count == 1


def test_db02_projection() -> None:
    source = StoredRow(RID(2, 0), (1, "Alice", 20))
    storage = FakeStorage([source])
    scan = SeqScanPlan(TABLE, UNKNOWN_SPAN)
    plan = ProjectPlan(scan, (1, 0, 1), ("name", "id", "name"), UNKNOWN_SPAN)
    operator = build_operator(plan, storage)

    operator.open()
    projected = operator.next()
    operator.close()

    assert projected == StoredRow(RID(2, 0), ("Alice", 1, "Alice"))
    assert projected is not source
    assert source.values == (1, "Alice", 20)


def test_db02_filter() -> None:
    rows = [StoredRow(RID(2 + index // 100, index % 100), (index, "n", 1)) for index in range(10_000)]
    storage = FakeStorage(rows)
    plan = FilterPlan(
        SeqScanPlan(TABLE, UNKNOWN_SPAN),
        BoundLiteral(False, DataType.BOOL, UNKNOWN_SPAN),
        UNKNOWN_SPAN,
    )
    operator = build_operator(plan, storage)

    operator.open()
    assert operator.next() is None

    assert storage.yield_count == 10_000
    assert storage.close_count == 1


def test_db02_tree() -> None:
    rows = [
        StoredRow(RID(2, 0), (1, "Minor", 17)),
        StoredRow(RID(2, 1), (2, "Alice", 20)),
        StoredRow(RID(2, 2), (3, "张三", 22)),
    ]
    original = tuple(rows)
    storage = FakeStorage(rows)
    predicate = BoundBinary(
        ">=",
        BoundColumn(2, "age", DataType.INT, UNKNOWN_SPAN),
        BoundLiteral(20, DataType.INT, UNKNOWN_SPAN),
        DataType.BOOL,
        UNKNOWN_SPAN,
    )
    filtered = FilterPlan(SeqScanPlan(TABLE, UNKNOWN_SPAN), predicate, UNKNOWN_SPAN)
    plan = ProjectPlan(filtered, (1,), ("name",), UNKNOWN_SPAN)
    operator = build_operator(plan, storage)

    operator.open()
    output = []
    while (row := operator.next()) is not None:
        output.append(row)

    assert [row.values for row in output] == [("Alice",), ("张三",)]
    assert rows == list(original)


def test_db02_close() -> None:
    rows = [
        StoredRow(RID(2, 0), (1, "Alice", 20)),
        StoredRow(RID(2, 1), (2,)),
    ]
    storage = FakeStorage(rows)
    predicate = BoundBinary(
        ">=",
        BoundColumn(2, "age", DataType.INT, UNKNOWN_SPAN),
        BoundLiteral(18, DataType.INT, UNKNOWN_SPAN),
        DataType.BOOL,
        UNKNOWN_SPAN,
    )
    operator = build_operator(
        FilterPlan(SeqScanPlan(TABLE, UNKNOWN_SPAN), predicate, UNKNOWN_SPAN), storage
    )

    operator.open()
    assert operator.next() == rows[0]
    with pytest.raises(ExecutionError):
        operator.next()
    operator.close()

    assert storage.close_count == 1


def test_db02_output_counter_resets_on_open() -> None:
    storage = FakeStorage([StoredRow(RID(2, 0), (1, "Alice", 20))])
    operator = build_operator(SeqScanPlan(TABLE, UNKNOWN_SPAN), storage)

    operator.open()
    assert operator.next() is not None
    assert operator.output_count == 1
    operator.close()
    operator.open()
    assert operator.output_count == 0
    assert operator.next() is not None
    assert operator.output_count == 1
    operator.close()
