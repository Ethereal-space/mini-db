from __future__ import annotations

import pytest

from minidb.storage.page import Page, PageFullError, RecordDeletedError


def test_reuse_payload_not_slot() -> None:
    page = Page.new(2, table_id=7)
    old_rids = [page.insert(b"a" * 300), page.insert(b"b" * 3700)]
    old_slot = page.slot(old_rids[0])
    page.mark_deleted(old_rids[0])
    before_slot_count = page.slot_count

    new_slot = page.insert(b"c" * 200)

    assert new_slot > max(old_rids)
    assert page.slot_count == before_slot_count + 1
    assert page.slot(new_slot).offset == old_slot.offset
    assert page.read(new_slot) == b"c" * 200
    with pytest.raises(RecordDeletedError):
        page.read(old_rids[0])


def test_hole_too_small() -> None:
    page = Page.new(3)
    page.insert(b"a" * 100)
    page.insert(b"b" * 3890)
    page.mark_deleted(0)
    before = page.to_bytes()

    with pytest.raises(PageFullError) as caught:
        page.insert(b"x" * 101)

    assert caught.value.code == "PAGE_FULL"
    assert page.to_bytes() == before


def test_slot_directory_space_required() -> None:
    page = Page.new(4)
    page.insert(b"a" * 4056)
    page.mark_deleted(0)
    before = page.to_bytes()

    with pytest.raises(PageFullError):
        page.insert(b"x")

    assert page.to_bytes() == before


def test_reopen_reuse_consistent(tmp_path) -> None:
    from minidb.storage.disk_manager import DiskManager

    path = tmp_path / "reuse.db"
    disk = DiskManager.open(path)
    page_id = disk.allocate_page()
    page = Page.new(page_id, table_id=9)
    page.insert(b"a" * 300)
    page.insert(b"b" * 3700)
    page.mark_deleted(0)
    new_slot = page.insert(b"c" * 200)
    disk.write_page(page_id, page.to_bytes())
    disk.close()

    with DiskManager.open(path) as reopened:
        restored = Page.from_bytes(reopened.read_page(page_id), expected_page_id=page_id)
        assert restored.slot_count == 3
        assert restored.slot(new_slot).offset == page.slot(new_slot).offset
        assert restored.read(new_slot) == b"c" * 200
        with pytest.raises(RecordDeletedError):
            restored.read(0)
