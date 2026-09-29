from __future__ import annotations

import pytest

from minidb.contracts import (
    BufferStats,
    ColumnMeta,
    CreateTablePlan,
    DataType,
    ExecutionError,
    InsertPlan,
    RID,
    StorageIOError,
    TableMeta,
    UNKNOWN_SPAN,
)
from minidb.runtime.command_executors import execute_create, execute_insert


COLUMNS = (
    ColumnMeta("id", DataType.INT, 0),
    ColumnMeta("name", DataType.VARCHAR, 1),
    ColumnMeta("age", DataType.INT, 2),
)
TABLE = TableMeta(1, "student", COLUMNS, 2)


class FakeCatalog:
    def __init__(
        self,
        log: list[str],
        *,
        exists: bool = False,
        register_error: Exception | None = None,
    ) -> None:
        self.log = log
        self.exists = exists
        self.register_error = register_error
        self.registered: list[TableMeta] = []
        self.allocate_calls = 0

    def table_exists(self, name: str) -> bool:
        self.log.append("exists")
        return self.exists

    def allocate_table_id(self) -> int:
        self.log.append("allocate")
        self.allocate_calls += 1
        return 1

    def register_table(self, table: TableMeta) -> None:
        self.log.append("register")
        if self.register_error is not None:
            raise self.register_error
        self.registered.append(table)

    def get_table(self, name: str) -> TableMeta:
        return TABLE

    def list_tables(self) -> list[TableMeta]:
        return list(self.registered)


class FakeStorage:
    def __init__(
        self,
        log: list[str],
        *,
        create_error: Exception | None = None,
        flush_error: Exception | None = None,
    ) -> None:
        self.log = log
        self.create_error = create_error
        self.flush_error = flush_error
        self.create_calls = 0
        self.flush_calls = 0
        self.inserted: list[tuple[TableMeta, tuple[int | str, ...]]] = []

    def create_table(self, table_id: int, columns: tuple[ColumnMeta, ...]) -> int:
        self.log.append("create")
        self.create_calls += 1
        if self.create_error is not None:
            raise self.create_error
        assert table_id == 1
        assert columns == COLUMNS
        return 2

    def insert(self, table: TableMeta, values: tuple[int | str, ...]) -> RID:
        self.log.append("insert")
        self.inserted.append((table, values))
        return RID(2, 7)

    def flush_all(self) -> None:
        self.log.append("flush")
        self.flush_calls += 1
        if self.flush_error is not None:
            raise self.flush_error

    def stats(self) -> BufferStats:
        return BufferStats()


def test_db03_create_order() -> None:
    log: list[str] = []
    catalog = FakeCatalog(log)
    storage = FakeStorage(log)

    result = execute_create(CreateTablePlan("student", COLUMNS, UNKNOWN_SPAN), catalog, storage)

    assert log == ["exists", "allocate", "create", "register", "flush"]
    assert catalog.registered == [TABLE]
    assert result.affected_rows == 0
    assert result.columns == result.rows == ()


def test_db03_duplicate() -> None:
    log: list[str] = []
    catalog = FakeCatalog(log, exists=True)
    storage = FakeStorage(log)

    with pytest.raises(ExecutionError) as captured:
        execute_create(CreateTablePlan("student", COLUMNS, UNKNOWN_SPAN), catalog, storage)

    assert captured.value.code == "TABLE_ALREADY_EXISTS"
    assert catalog.allocate_calls == 0
    assert storage.create_calls == 0


def test_db03_insert() -> None:
    log: list[str] = []
    storage = FakeStorage(log)
    values = (1, "张三", 21)

    result = execute_insert(InsertPlan(TABLE, values, UNKNOWN_SPAN), storage)

    assert storage.inserted == [(TABLE, values)]
    assert result.affected_rows == 1
    assert storage.flush_calls == 1
    assert result.trace[0].detail == "INSERT rid=2:7"


def test_db03_create_io() -> None:
    log: list[str] = []
    catalog = FakeCatalog(log)
    source_error = OSError("磁盘已满")
    storage = FakeStorage(log, create_error=source_error)

    with pytest.raises(StorageIOError) as captured:
        execute_create(CreateTablePlan("student", COLUMNS, UNKNOWN_SPAN), catalog, storage)

    assert captured.value.stage == "IO"
    assert captured.value.__cause__ is source_error
    assert catalog.registered == []
    assert "register" not in log
    assert "flush" not in log


def test_db03_flush_fail() -> None:
    log: list[str] = []
    storage = FakeStorage(log, flush_error=OSError("写回失败"))

    with pytest.raises(StorageIOError) as captured:
        execute_insert(InsertPlan(TABLE, (1, "Alice", 20), UNKNOWN_SPAN), storage)

    assert storage.inserted == [(TABLE, (1, "Alice", 20))]
    assert captured.value.code == "FLUSH_FAILED"
    assert captured.value.stage == "IO"
    assert "刷新" in captured.value.message


def test_db03_register_failure_stops_before_flush() -> None:
    log: list[str] = []
    register_error = ExecutionError("REGISTER_FAILED", "登记失败", UNKNOWN_SPAN)
    catalog = FakeCatalog(log, register_error=register_error)
    storage = FakeStorage(log)

    with pytest.raises(ExecutionError) as captured:
        execute_create(CreateTablePlan("student", COLUMNS, UNKNOWN_SPAN), catalog, storage)

    assert captured.value is register_error
    assert log == ["exists", "allocate", "create", "register"]
    assert storage.flush_calls == 0
