from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable

import pytest

from minidb.storage.constants import (
    CATALOG_PAGE_ID,
    DATA_PAGE_HEADER_SIZE,
    DATA_PAGE_MAGIC,
    DATA_PAGE_STRUCT,
    DATA_PAGE_VERSION,
    FORMAT_VERSION,
    INITIAL_CATALOG_HEAD,
    INITIAL_FREE_HEAD,
    INITIAL_NEXT_TABLE_ID,
    INITIAL_PAGE_COUNT,
    PAGE_SIZE,
    SUPERBLOCK_MAGIC,
    SUPERBLOCK_SIZE,
    SUPERBLOCK_STRUCT,
)
from minidb.storage.superblock import DatabaseFile, StorageFormatError, Superblock


def _file_hash(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_new_file_exact_layout(tmp_path) -> None:
    path = tmp_path / "mini.db"

    with DatabaseFile.open(path) as database:
        assert database.superblock == Superblock.initial()
        assert Superblock.decode(database.read_page(0)) == database.superblock

    raw = path.read_bytes()
    assert len(raw) == 2 * PAGE_SIZE
    assert raw[:8] == SUPERBLOCK_MAGIC
    assert SUPERBLOCK_STRUCT.unpack_from(raw, 0) == (
        SUPERBLOCK_MAGIC,
        FORMAT_VERSION,
        PAGE_SIZE,
        INITIAL_PAGE_COUNT,
        INITIAL_FREE_HEAD,
        INITIAL_CATALOG_HEAD,
        INITIAL_NEXT_TABLE_ID,
    )
    assert raw[SUPERBLOCK_SIZE:PAGE_SIZE] == bytes(PAGE_SIZE - SUPERBLOCK_SIZE)


def test_catalog_page_bootstrap(tmp_path) -> None:
    path = tmp_path / "mini.db"

    with DatabaseFile.open(path) as database:
        page = database.read_page(CATALOG_PAGE_ID)

    assert len(page) == PAGE_SIZE
    assert DATA_PAGE_STRUCT.unpack_from(page, 0) == (
        DATA_PAGE_MAGIC,
        CATALOG_PAGE_ID,
        -1,
        0,
        DATA_PAGE_HEADER_SIZE,
        PAGE_SIZE,
        0,
        0,
        DATA_PAGE_VERSION,
        0,
    )
    assert page[DATA_PAGE_HEADER_SIZE:] == bytes(PAGE_SIZE - DATA_PAGE_HEADER_SIZE)


def test_reopen_preserves_bytes(tmp_path) -> None:
    path = tmp_path / "mini.db"

    with DatabaseFile.open(path) as database:
        database.update_superblock(next_table_id=7)
        before_reopen = path.read_bytes()

    with DatabaseFile.open(path) as database:
        assert database.next_table_id == 7
        assert database.superblock.catalog_head == CATALOG_PAGE_ID
        after_reopen = path.read_bytes()

    assert after_reopen == before_reopen
    assert len(after_reopen) == 2 * PAGE_SIZE


def test_reject_corrupt_header_without_write(tmp_path) -> None:
    path = tmp_path / "mini.db"
    with DatabaseFile.open(path) as database:
        assert database.next_table_id == INITIAL_NEXT_TABLE_ID
    original = path.read_bytes()

    corruptions: tuple[tuple[str, Callable[[bytearray], None]], ...] = (
        ("magic", lambda data: data.__setitem__(slice(0, 8), b"BROKEN!!")),
        ("version", lambda data: struct.pack_into("<I", data, 8, FORMAT_VERSION + 1)),
        ("page_size", lambda data: struct.pack_into("<I", data, 12, PAGE_SIZE * 2)),
        ("truncated", lambda data: None),
    )

    for field, mutate in corruptions:
        if field == "truncated":
            path.write_bytes(original[:-1])
        else:
            data = bytearray(original)
            mutate(data)
            path.write_bytes(data)
        before_failed_open = path.read_bytes()

        with pytest.raises(StorageFormatError) as caught:
            DatabaseFile.open(path)

        error = caught.value
        assert error.stage == "STORAGE"
        assert error.span is None
        assert error.context["offset"] in {0, 8, 12, 16}
        if field != "truncated":
            assert error.context.get("field") == field
        assert path.read_bytes() == before_failed_open
        path.write_bytes(original)


def test_id_initial_value_is_read_only(tmp_path) -> None:
    path = tmp_path / "mini.db"

    with DatabaseFile.open(path) as database:
        database.update_superblock(next_table_id=7)
        assert database.next_table_id == 7
        assert max(4, database.next_table_id) == 7
        before_describe = _file_hash(path)
        description = database.describe_header()
        assert description["magic"] == {"offset": 0, "value": SUPERBLOCK_MAGIC}
        assert description["next_table_id"] == {"offset": 28, "value": 7}
        assert _file_hash(path) == before_describe
        with pytest.raises(AttributeError):
            database.next_table_id = 8  # type: ignore[misc]

    raw = bytearray(path.read_bytes())
    raw[0] = ord("X")
    path.write_bytes(raw)
    with pytest.raises(StorageFormatError) as caught:
        DatabaseFile.open(path)
    assert caught.value.span is None
