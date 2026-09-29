from __future__ import annotations

import pytest

from minidb.storage.page import Page, RecordDeletedError, SLOT_STRUCT
from minidb.storage.superblock import StorageFormatError


def _fragmented_page() -> Page:
    page = Page.new(8, table_id=4)
    for length in (200, 300, 400, 200, 400):
        page.insert(bytes([length % 251]) * length)
    page.mark_deleted(1)
    page.mark_deleted(3)
    return page


def test_rid_stable_after_compact() -> None:
    page = _fragmented_page()
    live_before = {slot_id: page.read(slot_id) for slot_id in (0, 2, 4)}
    slots_before = {slot_id: page.slot(slot_id) for slot_id in range(5)}

    page.compact()

    assert page.slot_count == 5
    for slot_id, payload in live_before.items():
        assert page.read(slot_id) == payload
        assert page.slot(slot_id).length == len(payload)
    for slot_id in (1, 3):
        assert page.slot(slot_id).is_deleted
        assert page.slot(slot_id).length == 0
        with pytest.raises(RecordDeletedError):
            page.read(slot_id)
    assert page.slot(0).offset != slots_before[0].offset or page.slot(2).offset != slots_before[2].offset


def test_compact_free_space() -> None:
    page = _fragmented_page()
    page.compact()

    assert page.free_start == 32 + 5 * 8 == 72
    assert page.free_end == 3096
    assert page.free_end - page.free_start == 3024


def test_compact_idempotent() -> None:
    page = _fragmented_page()
    page.compact()
    first = page.to_bytes()
    page.compact()
    assert page.to_bytes() == first


def test_compact_corrupt_page_rejected() -> None:
    page = Page.new(10)
    page.insert(b"first")
    page.insert(b"second")
    first = page.slot(0)
    # 在对象内部注入一个活记录重叠，compact 必须在替换临时页前拒绝。
    SLOT_STRUCT.pack_into(page._data, 32 + 8, first.offset, first.length, 0, 0)  # type: ignore[attr-defined]
    corrupt_before = bytes(page._data)  # type: ignore[attr-defined]

    with pytest.raises(StorageFormatError):
        page.compact()

    # compact 没有触碰原始字节；恢复后仍能正常验证。
    assert bytes(page._data) == corrupt_before  # type: ignore[attr-defined]
