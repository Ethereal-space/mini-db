from __future__ import annotations

from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta
from minidb.storage.free_space_map import FreeSpaceMap
from minidb.storage.table_heap import TableHeap


def _table(first_page_id: int, *, width: int = 1) -> TableMeta:
    columns = tuple(ColumnMeta(f"c{i}", DataType.INT, i) for i in range(width))
    return TableMeta(1, "items", columns, first_page_id)


def test_choose_existing_space() -> None:
    free_map = FreeSpaceMap()
    free_map.update(2, 20, table_id=1)
    free_map.update(3, 500, table_id=1)
    free_map.update(4, 50, table_id=1)

    assert free_map.find(200, table_id=1) == (3,)
    assert free_map.choose(200, table_id=1) == 3


def test_stale_overestimate(tmp_path) -> None:
    free_map = FreeSpaceMap()
    with TableHeap(tmp_path / "stale.db", free_space_map=free_map) as heap:
        first_page_id = heap.create_table(1, _table(2, width=1000).columns)
        table = _table(first_page_id, width=1000)
        first = heap.insert(table, tuple(0 for _ in range(1000)))
        # 真实页只剩几十字节，但故意注入一个过期高估。
        free_map.update(first.page_id, 500, table_id=1)
        second = heap.insert(table, tuple(1 for _ in range(1000)))
        assert second.page_id != first.page_id
        assert list(row.values[0] for row in heap.scan(table)) == [0, 1]
        assert free_map.capacity(first.page_id) is not None
        assert free_map.capacity(first.page_id) < 500


def test_rebuild_after_reopen(tmp_path) -> None:
    path = tmp_path / "rebuild.db"
    table: TableMeta
    with TableHeap(path) as heap:
        first_page_id = heap.create_table(1, _table(2).columns)
        table = _table(first_page_id)
        heap.insert(table, (10,))
        heap.insert(table, (20,))
        assert heap.free_space_map.snapshot(table_id=1)

    with TableHeap(path) as reopened:
        assert reopened.free_space_map.snapshot(table_id=1) == ()
        reopened.insert(table, (30,))
        assert reopened.free_space_map.snapshot(table_id=1)
        assert [row.values for row in reopened.scan(table)] == [(10,), (20,), (30,)]


def test_delete_without_reuse_no_gain(tmp_path) -> None:
    with TableHeap(tmp_path / "delete.db") as heap:
        first_page_id = heap.create_table(1, _table(2).columns)
        table = _table(first_page_id)
        rid = heap.insert(table, (1,))
        before = heap.free_space_map.capacity(first_page_id)
        heap.mark_delete(table, rid)
        after = heap.free_space_map.capacity(first_page_id)
        assert before == after
