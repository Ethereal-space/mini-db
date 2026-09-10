from __future__ import annotations

import struct

import pytest

from minidb.storage.constants import DATA_PAGE_STRUCT, PAGE_SIZE
from minidb.storage.page import (
    Page,
    PageFullError,
    RecordDeletedError,
    SlotNotFoundError,
    SLOT_STRUCT,
)
from minidb.storage.superblock import StorageFormatError


def test_exact_fit_page() -> None:
    page = Page.new(2, table_id=3)
    payload = b"x" * 4056
    assert page.insert(payload) == 0
    assert page.slot_count == 1
    assert page.slot(0).offset == 40
    assert page.slot(0).length == 4056
    assert page.free_start == page.free_end == 40
    before = page.to_bytes()
    with pytest.raises(PageFullError):
        page.insert(b"y")
    assert page.to_bytes() == before


def test_two_sided_growth() -> None:
    page = Page.new(2)
    assert page.insert(b"A" * 4000) == 0
    assert page.insert(b"B" * 48) == 1
    assert page.slot_count == 2
    assert page.slot(0).offset == 96
    assert page.slot(1).offset == 48
    assert page.free_start == page.free_end == 48
    assert page.read(0) == b"A" * 4000
    assert page.read(1) == b"B" * 48


def test_tombstone_preserves_rid() -> None:
    page = Page.new(5, table_id=8)
    assert [page.insert(value) for value in (b"a", b"bb", b"ccc")] == [0, 1, 2]
    old_slot_two = page.slot(2)
    assert page.mark_deleted(1) is True
    assert page.mark_deleted(1) is False
    assert page.slot_count == 3
    assert [slot_id for slot_id, _ in page.iter_live()] == [0, 2]
    assert page.slot(2) == old_slot_two
    assert page.read(2) == b"ccc"
    with pytest.raises(RecordDeletedError):
        page.read(1)


def test_serialize_roundtrip() -> None:
    page = Page.new(9, table_id=3, next_page_id=12)
    payload_a = "你好".encode("utf-8")
    page.insert(payload_a)
    page.insert(b"deleted")
    page.mark_deleted(1)
    encoded = page.serialize()
    assert len(encoded) == PAGE_SIZE
    restored = Page.deserialize(encoded, expected_page_id=9)
    assert restored.page_id == 9
    assert restored.table_id == 3
    assert restored.next_page_id == 12
    assert restored.slot(1).is_deleted
    assert list(restored.iter_live()) == [(0, payload_a)]
    assert restored.to_bytes() == encoded


def test_reject_corrupt_slot() -> None:
    page = Page.new(7)
    page.insert(b"payload")
    good = bytearray(page.to_bytes())

    bad_range = bytearray(good)
    struct.pack_into("<H", bad_range, 16, 4090)
    with pytest.raises(StorageFormatError) as caught:
        Page.from_bytes(bad_range, expected_page_id=7)
    assert caught.value.context["page_id"] == 7

    bad_payload = bytearray(good)
    SLOT_STRUCT.pack_into(bad_payload, 32, 4090, 10, 0, 0)
    with pytest.raises(StorageFormatError):
        Page.from_bytes(bad_payload, expected_page_id=7)

    bad_overlap = bytearray(good)
    page.insert(b"second")
    two = bytearray(page.to_bytes())
    first = page.slot(0)
    SLOT_STRUCT.pack_into(two, 40, first.offset, first.length, 0, 0)
    with pytest.raises(StorageFormatError):
        Page.from_bytes(two, expected_page_id=7)

    with pytest.raises(SlotNotFoundError):
        page.slot(99)


def test_live_count_is_logical_not_physical() -> None:
    page = Page.new(4)
    page.insert(b"a")
    page.insert(b"b")
    assert page.live_count == 2
    page.mark_deleted(0)
    assert page.live_count == 1
    assert page.slot_count == 2
