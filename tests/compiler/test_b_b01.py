from __future__ import annotations

import pytest

from minidb.compiler.catalog_service import CatalogService
from minidb.contracts.errors import MiniDBError, StorageError
from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta


class FakeRepository:
    def __init__(self, tables: list[TableMeta] | None = None) -> None:
        self.tables = list(tables or [])
        self.load_calls = 0
        self.save_calls = 0
        self.save_error: StorageError | None = None

    def load_tables(self) -> list[TableMeta]:
        self.load_calls += 1
        return list(self.tables)

    def save_table(self, table: TableMeta) -> None:
        self.save_calls += 1
        if self.save_error is not None:
            raise self.save_error
        self.tables.append(table)


def table(
    table_id: int,
    name: str,
    first_page_id: int = 3,
    columns: tuple[ColumnMeta, ...] | None = None,
) -> TableMeta:
    return TableMeta(
        table_id=table_id,
        name=name,
        columns=columns
        or (
            ColumnMeta("id", DataType.INT, 0),
            ColumnMeta("name", DataType.VARCHAR, 1),
        ),
        first_page_id=first_page_id,
    )


def raw_table(table_id: int, name: str, columns: tuple[ColumnMeta, ...]) -> TableMeta:
    value = object.__new__(TableMeta)
    object.__setattr__(value, "table_id", table_id)
    object.__setattr__(value, "name", name)
    object.__setattr__(value, "columns", columns)
    object.__setattr__(value, "first_page_id", 4)
    return value


def test_empty_catalog_allocates_unique_ids() -> None:
    repository = FakeRepository()
    service = CatalogService(repository)

    assert service.allocate_table_id() == 1
    assert service.allocate_table_id() == 2
    assert repository.load_calls == 1
    assert repository.save_calls == 0
    assert service.list_tables() == []


def test_reload_normalizes_and_preserves_schema() -> None:
    existing = table(7, "student", first_page_id=3)
    repository = FakeRepository([existing])

    low_bound = CatalogService(repository, initial_next_table_id=1)
    high_bound = CatalogService(repository, initial_next_table_id=12)

    assert low_bound.get_table("STUDENT") == existing
    assert low_bound.table_exists("Student") is True
    assert low_bound.allocate_table_id() == 8
    assert high_bound.allocate_table_id() == 12

    listed = low_bound.list_tables()
    listed.clear()
    assert low_bound.get_table("student").columns == existing.columns


def test_register_rejects_duplicate_before_save() -> None:
    repository = FakeRepository([table(1, "student")])
    service = CatalogService(repository)

    duplicate_names = table(2, "student")
    duplicate_id = table(1, "teacher")
    duplicate_column_names = raw_table(
        2,
        "teacher",
        (
            ColumnMeta("id", DataType.INT, 0),
            ColumnMeta("id", DataType.VARCHAR, 1),
        ),
    )

    with pytest.raises(MiniDBError, match="student"):
        service.register_table(duplicate_names)
    with pytest.raises(MiniDBError, match="表号"):
        service.register_table(duplicate_id)
    with pytest.raises(MiniDBError, match="列名"):
        service.register_table(duplicate_column_names)

    assert repository.save_calls == 0
    assert service.list_tables() == [table(1, "student")]


def test_list_tables_returns_stable_table_id_order() -> None:
    repository = FakeRepository([table(9, "zeta"), table(2, "alpha")])
    service = CatalogService(repository)

    assert [item.table_id for item in service.list_tables()] == [2, 9]


def test_repository_failure_does_not_publish_cache() -> None:
    repository = FakeRepository()
    repository.save_error = StorageError("WRITE_FAILED", "repository write failed")
    service = CatalogService(repository)
    new_table = table(1, "student")

    with pytest.raises(StorageError) as raised:
        service.register_table(new_table)

    assert raised.value is repository.save_error
    assert service.table_exists("student") is False
    assert service.list_tables() == []


def test_register_then_reload() -> None:
    repository = FakeRepository()
    service = CatalogService(repository)
    created = table(9, "student", first_page_id=11)

    service.register_table(created)
    reloaded = CatalogService(repository)

    assert repository.save_calls == 1
    assert reloaded.get_table("student") == created
    assert reloaded.get_table("student").columns == created.columns
    assert reloaded.allocate_table_id() == 10


def test_catalog_id_high_water() -> None:
    repository = FakeRepository([table(3, "student")])
    service = CatalogService(repository, initial_next_table_id=7)

    assert service.allocate_table_id() == 7

    exhausted = CatalogService(repository, initial_next_table_id=2_147_483_648)
    with pytest.raises(StorageError, match="ID_EXHAUSTED"):
        exhausted.allocate_table_id()
    assert repository.save_calls == 0
