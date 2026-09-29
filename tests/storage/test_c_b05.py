from __future__ import annotations

from pathlib import Path

import pytest

from minidb.contracts.errors import StorageError, StorageIOError
from minidb.storage.buffer_pool import BufferFullError, BufferPool
from minidb.storage.disk_manager import DiskManager


def _pages(path: Path, count: int = 3) -> DiskManager:
    disk = DiskManager.open(path)
    for _ in range(count):
        disk.allocate_page()
    return disk


def test_lru_fifo_a_b_a_c(tmp_path) -> None:
    path_lru = tmp_path / "lru.db"
    with _pages(path_lru) as disk:
        with BufferPool(disk, pool_size=2, policy="LRU") as pool:
            for page_id in (2, 3, 2, 4):
                with pool.fetch_page(page_id):
                    continue
            assert pool.stats().hits == 1
            assert pool.stats().misses == 3
            assert pool.stats().evictions == 1
            assert any(event.action == "evict" and event.page_id == 3 for event in pool.events)
    path_fifo = tmp_path / "fifo.db"
    with _pages(path_fifo) as disk:
        with BufferPool(disk, pool_size=2, policy="FIFO") as pool:
            for page_id in (2, 3, 2, 4):
                with pool.fetch_page(page_id):
                    continue
            assert pool.stats() == pool.stats().__class__(hits=1, misses=3, evictions=1, reads=3, writes=0)
            assert any(event.action == "evict" and event.page_id == 2 for event in pool.events)


def test_pin_and_exception_release(tmp_path) -> None:
    path = tmp_path / "pin.db"
    with _pages(path) as disk:
        pool = BufferPool(disk, pool_size=2)
        first = pool.fetch_page(2)
        second = pool.fetch_page(3)
        with first, second:
            with pytest.raises(BufferFullError):
                with pool.fetch_page(4):
                    _ = True
            assert pool.pin_count(2) == 1
            assert pool.pin_count(3) == 1
        assert pool.pinned_pages == 0
        with pytest.raises(RuntimeError):
            with pool.fetch_page(2):
                raise RuntimeError("body failure")
        assert pool.pin_count(2) == 0
        with pool.fetch_page(4):
            _ = True
        pool.close()


class FailingWriteDisk:
    def __init__(self, disk: DiskManager, failing_page: int | None = None) -> None:
        self.disk = disk
        self.failing_page = failing_page
        self.writes: list[int] = []

    def read_page(self, page_id: int) -> bytes:
        return self.disk.read_page(page_id)

    def write_page(self, page_id: int, data: bytes) -> None:
        self.writes.append(page_id)
        if page_id == self.failing_page:
            raise StorageIOError("WRITE_PAGE_FAILED", "injected write failure", span=None, context={"page_id": page_id})
        self.disk.write_page(page_id, data)

    def flush(self) -> None:
        self.disk.flush()


def test_dirty_eviction_survives_reopen(tmp_path) -> None:
    path = tmp_path / "dirty.db"
    with _pages(path, 2) as disk:
        pool = BufferPool(disk, pool_size=1)
        with pool.fetch_page(2) as frame:
            frame.data[:4] = b"EDIT"
            pool.mark_dirty(frame)
        with pool.fetch_page(3):
            _ = True
        assert pool.stats().writes == 1
        assert pool.stats().evictions == 1
        pool.close()
    with DiskManager.open(path) as reopened:
        assert reopened.read_page(2)[:4] == b"EDIT"


def test_failed_write_preserves_frame(tmp_path) -> None:
    path = tmp_path / "failed-write.db"
    disk = _pages(path, 2)
    failing = FailingWriteDisk(disk, failing_page=2)
    pool = BufferPool(failing, pool_size=1)
    with pool.fetch_page(2) as frame:
        frame.data[:3] = b"BAD"
        pool.mark_dirty(frame)
    with pytest.raises(StorageIOError):
        with pool.fetch_page(3):
            _ = True
    assert 2 in pool.frames
    assert pool.frames[2].dirty is True
    assert 3 not in pool.frames
    pool._closed = True
    disk.close()


def test_free_invalidates_dirty_cache(tmp_path) -> None:
    path = tmp_path / "invalidate.db"
    disk = DiskManager.open(path)
    page_id = disk.allocate_page()
    pool = BufferPool(disk, pool_size=2)
    with pool.fetch_page(page_id) as frame:
        frame.data[:3] = b"old"
        pool.mark_dirty(frame)
    disk.free_page(page_id)
    assert page_id not in pool.frames
    assert disk.allocate_page() == page_id
    with pool.fetch_page(page_id) as frame:
        frame.data[:3] = b"new"
        pool.mark_dirty(frame)
    pool.flush_all()
    with DiskManager.open(path) as reopened:
        assert reopened.read_page(page_id)[:3] == b"new"
    pool.close()

    disk2 = DiskManager.open(tmp_path / "pinned.db")
    p2 = disk2.allocate_page()
    pool2 = BufferPool(disk2, pool_size=1)
    context = pool2.fetch_page(p2)
    with context:
        with pytest.raises(StorageError) as caught:
            disk2.free_page(p2)
        assert caught.value.code == "PAGE_PINNED"
    pool2.close()
