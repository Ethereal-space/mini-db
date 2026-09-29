from __future__ import annotations

import hashlib
import struct

import pytest

from minidb.contracts.errors import StorageError
from minidb.storage.constants import DATA_PAGE_STRUCT, INVALID_PAGE_ID, PAGE_SIZE
from minidb.storage.disk_manager import DiskManager, FREE_PAGE_MAGIC
from minidb.storage.superblock import DatabaseFile, StorageFormatError


def _hash(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CoordinatorFake:
    def __init__(self) -> None:
        self.pins: dict[int, int] = {}
        self.dirty: set[int] = set()
        self.invalidated: list[int] = []

    def pin_count(self, page_id: int) -> int:
        return self.pins.get(page_id, 0)

    def invalidate(self, page_id: int) -> None:
        if self.pin_count(page_id):
            raise StorageError("PAGE_PINNED", "fake page is pinned", span=None, context={"page_id": page_id})
        self.invalidated.append(page_id)
        self.dirty.discard(page_id)


def test_allocate_read_write_offsets(tmp_path) -> None:
    path = tmp_path / "offsets.db"
    with DiskManager.open(path) as disk:
        page_a = disk.allocate_page()
        page_b = disk.allocate_page()
        disk.write_page(page_a, b"A" * PAGE_SIZE)
        disk.write_page(page_b, b"B" * PAGE_SIZE)
        assert (page_a, page_b) == (2, 3)
        assert disk.page_count == 4
        assert disk.read_page(2) == b"A" * PAGE_SIZE
        assert disk.read_page(3) == b"B" * PAGE_SIZE
    raw = path.read_bytes()
    assert len(raw) == 4 * PAGE_SIZE
    assert raw[2 * PAGE_SIZE : 3 * PAGE_SIZE] == b"A" * PAGE_SIZE
    assert raw[3 * PAGE_SIZE : 4 * PAGE_SIZE] == b"B" * PAGE_SIZE


def test_free_list_lifo_reuse(tmp_path) -> None:
    path = tmp_path / "reuse.db"
    with DiskManager.open(path) as disk:
        pages = [disk.allocate_page() for _ in range(3)]
        for page_id, marker in zip(pages, (b"a", b"b", b"c"), strict=True):
            disk.write_page(page_id, marker * PAGE_SIZE)
        disk.free_page(2)
        disk.free_page(4)
        assert disk.free_list_chain() == (4, 2)
        assert disk.allocate_page() == 4
        assert disk.read_page(4) == bytes(PAGE_SIZE)
        assert disk.allocate_page() == 2
        assert disk.read_page(2) == bytes(PAGE_SIZE)
        assert disk.page_count == 5
        assert disk.free_head == INVALID_PAGE_ID


def test_invalid_page_and_length(tmp_path) -> None:
    path = tmp_path / "invalid.db"
    with DiskManager.open(path) as disk:
        before = path.read_bytes()
        for page_id in (-1, disk.page_count):
            with pytest.raises(StorageError) as caught:
                disk.read_page(page_id)
            assert caught.value.context["page_id"] == page_id
        for length in (PAGE_SIZE - 1, PAGE_SIZE + 1):
            with pytest.raises(ValueError):
                disk.write_page(1, b"x" * length)
        for page_id in (0, 1):
            with pytest.raises(StorageError) as caught:
                disk.free_page(page_id)
            assert caught.value.code == "PROTECTED_PAGE"
        assert path.read_bytes() == before
        assert disk.page_count == 2


def test_duplicate_free_rejected_and_restart(tmp_path) -> None:
    path = tmp_path / "duplicate.db"
    with DiskManager.open(path) as disk:
        assert disk.allocate_page() == 2
        disk.free_page(2)
        with pytest.raises(StorageError) as caught:
            disk.free_page(2)
        assert caught.value.code == "DUPLICATE_FREE"
    with DiskManager.open(path) as disk:
        assert disk.allocate_page() == 2
        assert disk.allocate_page() == 3
        assert disk.free_list_chain() == ()


def test_free_cache_coordination_contract(tmp_path) -> None:
    path = tmp_path / "cache.db"
    coordinator = CoordinatorFake()
    with DiskManager.open(path, cache_coordinator=coordinator) as disk:
        page_id = disk.allocate_page()
        disk.write_page(page_id, b"old" + bytes(PAGE_SIZE - 3))
        coordinator.pins[page_id] = 1
        before = path.read_bytes()
        with pytest.raises(StorageError) as caught:
            disk.free_page(page_id)
        assert caught.value.code == "PAGE_PINNED"
        assert disk.free_head == INVALID_PAGE_ID
        assert path.read_bytes() == before
        coordinator.pins[page_id] = 0
        coordinator.dirty.add(page_id)
        disk.free_page(page_id)
        assert coordinator.invalidated == [page_id]
        assert disk.allocate_page() == page_id
        disk.write_page(page_id, b"new" + bytes(PAGE_SIZE - 3))
        disk.flush()
    with DiskManager.open(path) as reopened:
        assert reopened.read_page(page_id)[:3] == b"new"
        assert b"old" not in reopened.read_page(page_id)


def test_reject_live_or_linked_page_free(tmp_path) -> None:
    path = tmp_path / "in_use.db"
    with DiskManager.open(path) as disk:
        page_id = disk.allocate_page()
        page = bytearray(PAGE_SIZE)
        DATA_PAGE_STRUCT.pack_into(page, 0, b"MDPG", page_id, -1, 1, 32, 4088, 0, 4, 1, 0)
        disk.write_page(page_id, page)
        before = path.read_bytes()
        with pytest.raises(StorageError) as caught:
            disk.free_page(page_id)
        assert caught.value.code == "PAGE_HAS_RECORDS"
        assert _hash(path) == hashlib.sha256(before).hexdigest()
        with pytest.raises(StorageError) as caught:
            disk.free_page(page_id, linked=True)
        assert caught.value.code == "PAGE_STILL_IN_USE"
        assert disk.free_head == INVALID_PAGE_ID


def test_free_page_header_is_strict(tmp_path) -> None:
    path = tmp_path / "free-format.db"
    with DiskManager.open(path) as disk:
        disk.allocate_page()
        disk.free_page(2)
        page = disk.read_page(2)
    assert page[:4] == FREE_PAGE_MAGIC
    assert struct.unpack_from("<4sIiHHHHIII", page, 0)[1:3] == (2, -1)
    assert page[32:] == bytes(PAGE_SIZE - 32)
    with DatabaseFile.open(path) as database:
        assert database.page_count == 3
    with pytest.raises(StorageFormatError):
        broken = bytearray(path.read_bytes())
        broken[2 * PAGE_SIZE + 40] = 1
        path.write_bytes(broken)
        DiskManager.open(path)
