"""4096 字节 Slotted Page、Slot 目录和 tombstone。"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final, Iterator

from minidb.contracts.errors import StorageError

from .constants import (
    CATALOG_PAGE_ID,
    DATA_PAGE_HEADER_SIZE,
    DATA_PAGE_MAGIC,
    DATA_PAGE_STRUCT,
    DATA_PAGE_VERSION,
    INVALID_PAGE_ID,
    MAX_I32,
    PAGE_SIZE,
)
from .superblock import StorageFormatError


SLOT_FORMAT: Final[str] = "<HHHH"
SLOT_STRUCT = struct.Struct(SLOT_FORMAT)
SLOT_SIZE: Final[int] = SLOT_STRUCT.size
SLOT_DELETED: Final[int] = 1
TOMBSTONE_FLAG: Final[int] = SLOT_DELETED


def _error(error_type: type[StorageError], code: str, message: str, page_id: int, **context: object) -> StorageError:
    context = {"page_id": page_id, **context}
    if error_type is StorageFormatError:
        return StorageFormatError(code, message, context)
    return error_type(code, message, span=None, context=context)


class PageFullError(StorageError):
    """页没有同时容纳新记录和新 Slot 的空间。"""


class SlotNotFoundError(StorageError):
    """请求的槽号不在页的 Slot 目录中。"""


class RecordDeletedError(StorageError):
    """请求读取的槽已被逻辑删除。"""


@dataclass(frozen=True, slots=True)
class Slot:
    offset: int
    length: int
    flags: int = 0
    reserved: int = 0

    @property
    def is_deleted(self) -> bool:
        return bool(self.flags & SLOT_DELETED)

    @property
    def deleted(self) -> bool:
        return self.is_deleted


class Page:
    """可验证、可往返序列化的页内槽目录。"""

    def __init__(
        self,
        page_id: int,
        table_id: int = 0,
        next_page_id: int = INVALID_PAGE_ID,
        *,
        flags: int = 0,
        version: int = DATA_PAGE_VERSION,
        data: bytes | bytearray | memoryview | None = None,
    ) -> None:
        if data is not None:
            parsed = type(self).from_bytes(data, expected_page_id=page_id)
            self._data = parsed._data
            self._page_id = parsed._page_id
            self._table_id = parsed._table_id
            self._next_page_id = parsed._next_page_id
            self._flags = parsed._flags
            self._version = parsed._version
            self._slots = parsed._slots
            self._free_start = parsed._free_start
            self._free_end = parsed._free_end
            return
        self._validate_identity(page_id, table_id, next_page_id, flags, version)
        self._page_id = page_id
        self._table_id = table_id
        self._next_page_id = next_page_id
        self._flags = flags
        self._version = version
        self._slots: list[Slot] = []
        self._free_start = DATA_PAGE_HEADER_SIZE
        self._free_end = PAGE_SIZE
        self._data = bytearray(PAGE_SIZE)
        self._sync_header()

    @classmethod
    def new(
        cls,
        page_id: int,
        table_id: int = 0,
        next_page_id: int = INVALID_PAGE_ID,
        *,
        flags: int = 0,
    ) -> "Page":
        return cls(page_id, table_id, next_page_id, flags=flags)

    @classmethod
    def from_bytes(
        cls,
        data: bytes | bytearray | memoryview,
        *,
        expected_page_id: int | None = None,
        page_id: int | None = None,
    ) -> "Page":
        if expected_page_id is None:
            expected_page_id = page_id
        raw = bytes(data)
        page_id_for_error = expected_page_id if expected_page_id is not None else 1
        if len(raw) != PAGE_SIZE:
            raise _error(
                StorageFormatError,
                "BAD_PAGE_LENGTH",
                f"页必须恰好为 {PAGE_SIZE} 字节，实际为 {len(raw)}",
                page_id_for_error,
                expected_length=PAGE_SIZE,
                actual_length=len(raw),
            )
        try:
            magic, page_id, next_page_id, slot_count, free_start, free_end, flags, table_id, version, reserved = (
                DATA_PAGE_STRUCT.unpack_from(raw, 0)
            )
        except struct.error as error:
            raise _error(StorageFormatError, "BAD_PAGE_HEADER", f"页头解码失败：{error}", page_id_for_error) from error
        if expected_page_id is not None and page_id != expected_page_id:
            raise _error(
                StorageFormatError,
                "PAGE_ID_MISMATCH",
                f"页号不匹配：期望 {expected_page_id}，实际 {page_id}",
                expected_page_id,
                actual_page_id=page_id,
                offset=4,
            )
        if magic != DATA_PAGE_MAGIC:
            raise _error(StorageFormatError, "BAD_PAGE_MAGIC", f"数据页 magic 错误：{magic!r}", page_id, offset=0)
        if page_id < CATALOG_PAGE_ID or page_id > MAX_I32:
            raise _error(StorageFormatError, "BAD_PAGE_ID", f"数据页 page_id 非法：{page_id}", page_id, offset=4)
        if next_page_id != INVALID_PAGE_ID and (next_page_id <= CATALOG_PAGE_ID or next_page_id > MAX_I32):
            raise _error(
                StorageFormatError,
                "BAD_NEXT_PAGE_ID",
                f"next_page_id 必须为 -1 或大于 1：{next_page_id}",
                page_id,
                offset=8,
            )
        if version != DATA_PAGE_VERSION:
            raise _error(
                StorageFormatError,
                "UNSUPPORTED_PAGE_VERSION",
                f"不支持的数据页版本：{version}",
                page_id,
                offset=24,
            )
        if reserved != 0:
            raise _error(StorageFormatError, "NONZERO_PAGE_RESERVED", "页头 reserved 必须为零", page_id, offset=28)
        if not DATA_PAGE_HEADER_SIZE <= free_start <= free_end <= PAGE_SIZE:
            raise _error(
                StorageFormatError,
                "BAD_FREE_RANGE",
                f"free_start/free_end 非法：{free_start}/{free_end}",
                page_id,
                offset=16,
            )
        slot_end = DATA_PAGE_HEADER_SIZE + slot_count * SLOT_SIZE
        if slot_end > free_end:
            raise _error(
                StorageFormatError,
                "SLOT_RECORD_OVERLAP",
                f"Slot 目录末端 {slot_end} 超过记录区起点 {free_end}",
                page_id,
                slot_end=slot_end,
                free_end=free_end,
            )
        if free_start != slot_end:
            raise _error(
                StorageFormatError,
                "BAD_FREE_START",
                f"free_start 应为 {slot_end}，实际为 {free_start}",
                page_id,
                offset=16,
            )
        slots: list[Slot] = []
        records: list[tuple[int, int, int]] = []
        for slot_id in range(slot_count):
            slot_offset = DATA_PAGE_HEADER_SIZE + slot_id * SLOT_SIZE
            try:
                offset, length, slot_flags, slot_reserved = SLOT_STRUCT.unpack_from(raw, slot_offset)
            except struct.error as error:
                raise _error(
                    StorageFormatError,
                    "BAD_SLOT_HEADER",
                    f"slot {slot_id} 解码失败：{error}",
                    page_id,
                    slot_id=slot_id,
                    offset=slot_offset,
                ) from error
            if slot_flags & ~SLOT_DELETED:
                raise _error(
                    StorageFormatError,
                    "BAD_SLOT_FLAGS",
                    f"slot {slot_id} 含未知 flags：{slot_flags}",
                    page_id,
                    slot_id=slot_id,
                    offset=slot_offset + 4,
                )
            if slot_reserved != 0:
                raise _error(
                    StorageFormatError,
                    "NONZERO_SLOT_RESERVED",
                    f"slot {slot_id} reserved 必须为零",
                    page_id,
                    slot_id=slot_id,
                    offset=slot_offset + 6,
                )
            if offset < free_end or offset + length > PAGE_SIZE:
                raise _error(
                    StorageFormatError,
                    "SLOT_OUT_OF_BOUNDS",
                    f"slot {slot_id} 记录范围非法：offset={offset}, length={length}, free_end={free_end}",
                    page_id,
                    slot_id=slot_id,
                    offset=slot_offset,
                )
            slots.append(Slot(offset, length, slot_flags, slot_reserved))
            if length:
                records.append((offset, offset + length, slot_id))
        records.sort()
        for previous, current in zip(records, records[1:]):
            if current[0] < previous[1]:
                raise _error(
                    StorageFormatError,
                    "OVERLAPPING_RECORDS",
                    f"slot {previous[2]} 与 slot {current[2]} 的记录范围重叠",
                    page_id,
                    first_slot=previous[2],
                    second_slot=current[2],
                )
        result = cls.__new__(cls)
        result._data = bytearray(raw)
        result._page_id = page_id
        result._table_id = table_id
        result._next_page_id = next_page_id
        result._flags = flags
        result._version = version
        result._slots = slots
        result._free_start = free_start
        result._free_end = free_end
        return result

    deserialize = from_bytes
    parse = from_bytes

    @staticmethod
    def _validate_identity(page_id: int, table_id: int, next_page_id: int, flags: int, version: int) -> None:
        if not isinstance(page_id, int) or isinstance(page_id, bool) or not CATALOG_PAGE_ID <= page_id <= MAX_I32:
            raise ValueError("page_id 必须是大于等于 1 的整数")
        if not isinstance(table_id, int) or isinstance(table_id, bool) or not 0 <= table_id <= 0xFFFFFFFF:
            raise ValueError("table_id 必须适合无符号 32 位字段")
        if next_page_id != INVALID_PAGE_ID and (
            not isinstance(next_page_id, int) or next_page_id <= CATALOG_PAGE_ID or next_page_id > MAX_I32
        ):
            raise ValueError("next_page_id 必须为 -1 或大于 1")
        if not 0 <= flags <= 0xFFFF:
            raise ValueError("flags 必须适合无符号 16 位字段")
        if version != DATA_PAGE_VERSION:
            raise ValueError(f"只支持数据页版本 {DATA_PAGE_VERSION}")

    @property
    def page_id(self) -> int:
        return self._page_id

    @property
    def table_id(self) -> int:
        return self._table_id

    @property
    def next_page_id(self) -> int:
        return self._next_page_id

    @next_page_id.setter
    def next_page_id(self, value: int) -> None:
        if value != INVALID_PAGE_ID and (
            not isinstance(value, int) or value <= CATALOG_PAGE_ID or value > MAX_I32
        ):
            raise ValueError("next_page_id 必须为 -1 或大于 1")
        self._next_page_id = value
        self._sync_header()

    @property
    def flags(self) -> int:
        return self._flags

    @property
    def version(self) -> int:
        return self._version

    @property
    def slot_count(self) -> int:
        return len(self._slots)

    @property
    def free_start(self) -> int:
        return self._free_start

    @property
    def free_end(self) -> int:
        return self._free_end

    @property
    def data(self) -> bytes:
        return bytes(self._data)

    @property
    def slots(self) -> tuple[Slot, ...]:
        return tuple(self._slots)

    @property
    def live_count(self) -> int:
        return sum(not slot.is_deleted for slot in self._slots)

    def slot(self, slot_id: int) -> Slot:
        if not isinstance(slot_id, int) or isinstance(slot_id, bool) or not 0 <= slot_id < len(self._slots):
            raise _error(SlotNotFoundError, "SLOT_NOT_FOUND", f"slot_id 不存在：{slot_id}", self._page_id, slot_id=slot_id)
        return self._slots[slot_id]

    get_slot = slot

    def insert(self, payload: bytes | bytearray | memoryview) -> int:
        raw_payload = bytes(payload)
        required = SLOT_SIZE + len(raw_payload)
        available = self._free_end - self._free_start
        if required > available:
            raise _error(
                PageFullError,
                "PAGE_FULL",
                f"页空间不足：需要 {required} 字节，剩余 {available} 字节",
                self._page_id,
                payload_length=len(raw_payload),
                available=available,
            )
        slot_id = len(self._slots)
        old_free_end = self._free_end
        record_offset = old_free_end - len(raw_payload)
        slot = Slot(record_offset, len(raw_payload), 0, 0)
        self._data[record_offset:old_free_end] = raw_payload
        SLOT_STRUCT.pack_into(self._data, self._free_start, slot.offset, slot.length, slot.flags, slot.reserved)
        self._slots.append(slot)
        self._free_start += SLOT_SIZE
        self._free_end = record_offset
        self._sync_header()
        return slot_id

    def read(self, slot_id: int) -> bytes:
        slot = self.slot(slot_id)
        if slot.is_deleted:
            raise _error(
                RecordDeletedError,
                "RECORD_DELETED",
                f"slot {slot_id} 已被逻辑删除",
                self._page_id,
                slot_id=slot_id,
            )
        return bytes(self._data[slot.offset : slot.offset + slot.length])

    get = read
    read_slot = read

    def mark_deleted(self, slot_id: int) -> bool:
        slot = self.slot(slot_id)
        if slot.is_deleted:
            return False
        updated = Slot(slot.offset, slot.length, slot.flags | SLOT_DELETED, slot.reserved)
        self._slots[slot_id] = updated
        SLOT_STRUCT.pack_into(self._data, DATA_PAGE_HEADER_SIZE + slot_id * SLOT_SIZE, updated.offset, updated.length, updated.flags, updated.reserved)
        return True

    delete = mark_deleted
    delete_slot = mark_deleted

    def iter_live(self) -> Iterator[tuple[int, bytes]]:
        for slot_id, slot in enumerate(self._slots):
            if not slot.is_deleted:
                yield slot_id, bytes(self._data[slot.offset : slot.offset + slot.length])

    def validate(self) -> None:
        parsed = type(self).from_bytes(self.to_bytes(), expected_page_id=self._page_id)
        if parsed._slots != self._slots:
            raise _error(StorageFormatError, "PAGE_STATE_MISMATCH", "页内状态与序列化字节不一致", self._page_id)

    def to_bytes(self) -> bytes:
        self._sync_header()
        return bytes(self._data)

    serialize = to_bytes

    def _sync_header(self) -> None:
        DATA_PAGE_STRUCT.pack_into(
            self._data,
            0,
            DATA_PAGE_MAGIC,
            self._page_id,
            self._next_page_id,
            len(self._slots),
            self._free_start,
            self._free_end,
            self._flags,
            self._table_id,
            self._version,
            0,
        )


SlottedPage = Page
SlotRecord = Slot


__all__ = [
    "Page",
    "PageFullError",
    "RecordDeletedError",
    "SLOT_DELETED",
    "SLOT_FORMAT",
    "SLOT_SIZE",
    "SLOT_STRUCT",
    "Slot",
    "SlotNotFoundError",
    "SlottedPage",
    "SlotRecord",
    "StorageFormatError",
    "TOMBSTONE_FLAG",
]
