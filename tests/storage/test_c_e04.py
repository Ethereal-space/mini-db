from __future__ import annotations

import hashlib

import pytest

from minidb.storage.checksum import ChecksumError, crc32, seal_page, verify_page
from minidb.storage.constants import CHECKSUM_OFFSET, DATA_PAGE_VERSION_V2, PAGE_SIZE
from minidb.storage.page import Page, PageFullError
from minidb.storage.superblock import DatabaseFile, StorageFormatError


def test_v2_crc_roundtrip(tmp_path) -> None:
    path = tmp_path / "v2.db"
    with DatabaseFile.open_v2(path) as database:
        page_id = database.append_page(bytes(PAGE_SIZE))
        page = Page.new(page_id, table_id=3, version=DATA_PAGE_VERSION_V2)
        page.insert("你好".encode("utf-8"))
        database.write_page(page_id, page.to_bytes())
        assert verify_page(database.read_page(page_id), page_id=page_id) == crc32(page.to_bytes())

    with DatabaseFile.open_v2(path) as reopened:
        restored = Page.from_bytes(reopened.read_page(page_id), expected_page_id=page_id)
        assert restored.version == DATA_PAGE_VERSION_V2
        assert restored.read(0) == "你好".encode("utf-8")
        assert restored.to_bytes() == reopened.read_page(page_id)


@pytest.mark.parametrize("offset", [4, 100])
def test_detect_bit_flip(tmp_path, offset: int) -> None:
    path = tmp_path / f"flip-{offset}.db"
    with DatabaseFile.open_v2(path) as database:
        page_id = database.append_page(bytes(PAGE_SIZE))
        page = Page.new(page_id, version=DATA_PAGE_VERSION_V2)
        page.insert(b"payload")
        database.write_page(page_id, page.to_bytes())

    raw = bytearray(path.read_bytes())
    raw[page_id * PAGE_SIZE + offset] ^= 0x01
    path.write_bytes(raw)
    with DatabaseFile.open_v2(path) as database:
        with pytest.raises(ChecksumError) as caught:
            database.read_page(page_id)
    assert caught.value.context["page_id"] == page_id
    assert "expected_crc" in caught.value.context
    assert "actual_crc" in caught.value.context


def test_v2_exact_capacity() -> None:
    page = Page.new(2, version=DATA_PAGE_VERSION_V2)
    assert page.payload_limit == CHECKSUM_OFFSET == 4092
    assert page.insert(b"x" * 4052) == 0
    assert page.free_start == page.free_end == 40
    before = page.to_bytes()
    with pytest.raises(PageFullError):
        page.insert(b"y")
    assert page.to_bytes() == before
    assert verify_page(before, page_id=2) == crc32(before)


def test_v1_not_silently_upgraded(tmp_path) -> None:
    path = tmp_path / "v1.db"
    with DatabaseFile.open(path) as database:
        assert database.page_version == 1
    before = hashlib.sha256(path.read_bytes()).digest()
    with DatabaseFile.open(path) as reopened:
        assert reopened.page_version == 1
    assert hashlib.sha256(path.read_bytes()).digest() == before
    with pytest.raises(StorageFormatError) as caught:
        DatabaseFile.open_v2(path)
    assert caught.value.code == "VERSION_MODE_MISMATCH"
