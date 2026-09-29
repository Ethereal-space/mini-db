from __future__ import annotations

import pytest

from minidb.contracts.errors import StorageError
from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta
from minidb.storage.catalog_repository import CATALOG_COLUMNS, CATALOG_FIRST_PAGE_ID, CATALOG_TABLE_ID, PageCatalogRepository
from minidb.storage.page import Page
from minidb.storage.row_codec import RowCodec, RowTooLargeError
from minidb.storage.superblock import StorageFormatError
from minidb.storage.table_heap import TableHeap


def schema_int_text() -> tuple[ColumnMeta, ...]:
    return (ColumnMeta("id", DataType.INT, 0), ColumnMeta("name", DataType.VARCHAR, 1))


def make_table(heap: TableHeap, table_id: int, name: str = "student") -> TableMeta:
    columns = schema_int_text()
    first_page_id = heap.create_table(table_id, columns)
    return TableMeta(table_id, name, columns, first_page_id)


def test_thousand_rows_cross_pages(tmp_path) -> None:
    path = tmp_path / "rows.db"
    with TableHeap(path, buffer_pool_size=2) as heap:
        table = make_table(heap, 1)
        rids = [heap.insert(table, (index, "x" * 160)) for index in range(1000)]
        assert RowCodec.encoded_size(table.columns, (0, "x" * 160)) == 166
        assert heap.table_page_count(table) == 44
        assert len(set(rids)) == 1000
        rows = list(heap.scan(table))
        assert len(rows) == 1000
        assert [row.values for row in rows[:3]] == [(0, "x" * 160), (1, "x" * 160), (2, "x" * 160)]
        assert [row.rid for row in rows] == rids
        assert heap.buffer_pool.pinned_pages == 0


def test_delete_and_restart(tmp_path) -> None:
    path = tmp_path / "restart.db"
    columns = schema_int_text()
    with TableHeap(path, buffer_pool_size=2) as heap:
        table = make_table(heap, 1)
        repository = PageCatalogRepository(heap)
        repository.save_table(table)
        rids = [heap.insert(table, value) for value in ((1, "a"), (2, "b"), (3, "c"))]
        heap.mark_delete(table, rids[1])
        heap.flush_all()
        first_page = table.first_page_id
    with TableHeap(path, buffer_pool_size=2) as reopened_heap:
        reopened = PageCatalogRepository(reopened_heap)
        tables = reopened.load_tables()
        assert tables == [TableMeta(1, "student", columns, first_page)]
        assert [row.values for row in reopened_heap.scan(tables[0])] == [(1, "a"), (3, "c")]
        page = Page.from_bytes(reopened_heap.disk.read_page(first_page), expected_page_id=first_page)
        assert page.slot(rids[1].slot_id).is_deleted


def test_catalog_uses_pages_and_no_sidecar(tmp_path) -> None:
    path = tmp_path / "catalog-pages.db"
    with TableHeap(path, buffer_pool_size=2) as heap:
        repository = PageCatalogRepository(heap)
        for table_id in range(1, 18):
            columns = tuple(ColumnMeta(f"column_{table_id}_{index}", DataType.VARCHAR, index) for index in range(6))
            first_page_id = heap.create_table(table_id, columns)
            table = TableMeta(table_id, f"table_{table_id}", columns, first_page_id)
            repository.save_table(table)
        assert heap.catalog_page_count() >= 2
        rows = list(heap.scan_raw(CATALOG_TABLE_ID, CATALOG_FIRST_PAGE_ID, CATALOG_COLUMNS))
        assert len(rows) == 17 * 6
        assert all(RowCodec.decode(CATALOG_COLUMNS, RowCodec.encode(CATALOG_COLUMNS, row.values)) == row.values for row in rows)
    names = {item.name for item in tmp_path.iterdir()}
    assert names == {"catalog-pages.db"}


def test_scan_early_stop_unpins(tmp_path) -> None:
    path = tmp_path / "early-stop.db"
    with TableHeap(path, buffer_pool_size=1) as heap:
        table = make_table(heap, 1)
        for index in range(100):
            heap.insert(table, (index, "x" * 160))
        iterator = heap.scan(table)
        first = next(iterator)
        assert first.values == (0, "x" * 160)
        assert heap.buffer_pool.pinned_pages == 0
        second_page = next(page for page in range(table.first_page_id + 1, heap.page_count))
        with heap.buffer_pool.fetch_page(second_page):
            _ = True
        assert heap.buffer_pool.pinned_pages == 0
        iterator.close()
        assert heap.buffer_pool.pinned_pages == 0


def test_reject_wrong_rid_and_catalog_corruption(tmp_path) -> None:
    path = tmp_path / "wrong-rid.db"
    with TableHeap(path, buffer_pool_size=2) as heap:
        table_a = make_table(heap, 1, "alpha")
        table_b = make_table(heap, 2, "bravo")
        rid_a = heap.insert(table_a, (1, "a"))
        rid_b = heap.insert(table_b, (2, "b"))
        with pytest.raises(StorageError) as caught:
            heap.mark_delete(table_a, rid_b)
        assert caught.value.code == "RID_NOT_IN_TABLE"
        assert [row.values for row in heap.scan(table_a)] == [(1, "a")]
        assert [row.values for row in heap.scan(table_b)] == [(2, "b")]
        repository = PageCatalogRepository(heap)
        repository.save_table(table_a)
        repository.save_table(table_b)
        rows = list(heap.scan_raw(CATALOG_TABLE_ID, CATALOG_FIRST_PAGE_ID, CATALOG_COLUMNS))
        ordinal_row = next(row for row in rows if row.values[0] == 1 and row.values[3] == 1)
        raw = bytearray(heap.disk.read_page(ordinal_row.rid.page_id))
        replacement = RowCodec.encode(CATALOG_COLUMNS, (1, "alpha", table_a.first_page_id, 2, "name", "VARCHAR"))
        slot = Page.from_bytes(raw, expected_page_id=ordinal_row.rid.page_id).slot(ordinal_row.rid.slot_id)
        assert len(replacement) == slot.length
        raw[slot.offset : slot.offset + slot.length] = replacement
        heap.buffer_pool.invalidate(ordinal_row.rid.page_id)
        heap.disk.write_page(ordinal_row.rid.page_id, raw)
        with pytest.raises(StorageFormatError) as caught_catalog:
            repository.load_tables()
        assert "ordinal" in str(caught_catalog.value)


def test_oversize_row_before_allocation(tmp_path) -> None:
    path = tmp_path / "oversize.db"
    columns = tuple(ColumnMeta(f"c{index}", DataType.INT, index) for index in range(1015))
    with TableHeap(path, buffer_pool_size=2) as heap:
        first_page_id = heap.create_table(1, columns)
        table = TableMeta(1, "wide", columns, first_page_id)
        before_bytes = path.read_bytes()
        before_count = heap.page_count
        with pytest.raises(RowTooLargeError):
            heap.insert(table, tuple(range(1015)))
        assert heap.page_count == before_count
        assert heap.table_page_count(table) == 1
        assert path.read_bytes() == before_bytes
