from __future__ import annotations

import pytest

from minidb.storage.buffer_pool import BufferFullError, BufferPool
from minidb.storage.disk_manager import DiskManager
from minidb.storage.replacer import ClockReplacer


def _pages(path, count: int = 3) -> DiskManager:
    disk = DiskManager.open(path)
    for _ in range(count):
        disk.allocate_page()
    return disk


def test_second_chance() -> None:
    replacer = ClockReplacer(2)
    replacer.record_load(2)
    replacer.record_load(3)
    replacer.set_reference(2, True)
    replacer.set_reference(3, False)

    assert replacer.hand == 2
    assert replacer.choose_victim() == 3
    assert replacer.reference_bit(2) is False
    assert replacer.hand == 2
    assert replacer.scan_steps == 2


def test_skip_pinned() -> None:
    replacer = ClockReplacer(2)
    replacer.record_load(2)
    replacer.record_load(3)
    replacer.set_reference(2, False)
    replacer.set_reference(3, False)
    replacer.pin(2)

    assert replacer.choose_victim() == 3
    assert replacer.pin_count(2) == 1
    assert 2 in replacer.page_ids


def test_all_pinned_bounded() -> None:
    replacer = ClockReplacer(2)
    replacer.record_load(2)
    replacer.record_load(3)
    replacer.pin(2)
    replacer.pin(3)

    assert replacer.choose_victim() is None
    assert replacer.scan_steps == 4


def test_dirty_clock_eviction(tmp_path) -> None:
    path = tmp_path / "clock.db"
    with _pages(path, 2) as disk:
        with BufferPool(disk, pool_size=1, policy="CLOCK") as pool:
            with pool.fetch_page(2) as frame:
                frame.data[:4] = b"EDIT"
                pool.mark_dirty(frame)
            with pool.fetch_page(3):
                _ = True
            assert pool.stats().writes == 1
            assert any(event.action == "evict" and event.page_id == 2 for event in pool.events)
            assert pool.replacer.policy.value == "CLOCK"
    with DiskManager.open(path) as reopened:
        assert reopened.read_page(2)[:4] == b"EDIT"
