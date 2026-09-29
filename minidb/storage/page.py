"""4096 字节 Slotted Page、Slot 目录和 tombstone。"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final, Iterator

from minidb.contracts.errors import StorageError

from .constants import (
    CATALOG_PAGE_ID,
    CHECKSUM_OFFSET,
    DATA_PAGE_HEADER_SIZE,
    DATA_PAGE_MAGIC,
    DATA_PAGE_STRUCT,
    DATA_PAGE_VERSION,
    DATA_PAGE_VERSION_V2,
    SUPPORTED_DATA_PAGE_VERSIONS,
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
    """供页解析和操作分支统一构造错误；附加 page_id 并保留格式错误类型。"""

    context = {"page_id": page_id, **context}
    if error_type is StorageFormatError:
        return StorageFormatError(code, message, context)
    return error_type(code, message, span=None, context=context)


class PageFullError(StorageError):
    """由插入路径在 Slot 与记录空间不足时抛出，供 TableHeap 换页重试。"""


class SlotNotFoundError(StorageError):
    """由 slot 校验非法槽号时抛出，供 RID 读取和删除路径报告错误。"""


class RecordDeletedError(StorageError):
    """由 read 遇到 tombstone 时抛出，阻止上层返回已删除记录。"""


@dataclass(frozen=True, slots=True)
class Slot:
    """描述记录在页内的偏移、长度和标志；由 Page 解析或插入时创建。"""

    offset: int
    length: int
    flags: int = 0
    reserved: int = 0

    @property
    def is_deleted(self) -> bool:
        """供扫描和读取判断 tombstone；检查 flags 中的删除位。"""

        return bool(self.flags & SLOT_DELETED)

    @property
    def deleted(self) -> bool:
        """提供兼容只读别名；直接返回 is_deleted 的判断结果。"""

        return self.is_deleted


class Page:
    """由 TableHeap 读写的 4096 字节 Slotted Page，管理槽目录与记录区。"""

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
        """由新页或兼容加载路径调用；解析现有字节，或建立空页并同步页头。"""

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
        self._free_end = CHECKSUM_OFFSET if version == DATA_PAGE_VERSION_V2 else PAGE_SIZE
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
        version: int = DATA_PAGE_VERSION,
    ) -> "Page":
        """供 DiskManager/TableHeap 创建空页；把标识字段交给构造器初始化。"""

        return cls(page_id, table_id, next_page_id, flags=flags, version=version)

    @classmethod
    def from_bytes(
        cls,
        data: bytes | bytearray | memoryview,
        *,
        expected_page_id: int | None = None,
        page_id: int | None = None,
    ) -> "Page":
        """供读盘路径解码页；先验长度和校验和，再校验页头、槽及记录边界。"""

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
        # 版本字段位于固定页头内；v2 先验证整页 CRC，再解释 page_id、
        # slot_count 等其余字段，避免损坏计数驱动越界读取。
        raw_version = int.from_bytes(raw[24:28], "little")
        if raw_version == DATA_PAGE_VERSION_V2:
            from .checksum import verify_page

            verify_page(raw, page_id=page_id_for_error)
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
        if version not in SUPPORTED_DATA_PAGE_VERSIONS:
            raise _error(
                StorageFormatError,
                "UNSUPPORTED_PAGE_VERSION",
                f"不支持的数据页版本：{version}",
                page_id,
                offset=24,
            )
        if reserved != 0:
            raise _error(StorageFormatError, "NONZERO_PAGE_RESERVED", "页头 reserved 必须为零", page_id, offset=28)
        payload_limit = CHECKSUM_OFFSET if version == DATA_PAGE_VERSION_V2 else PAGE_SIZE
        if not DATA_PAGE_HEADER_SIZE <= free_start <= free_end <= payload_limit:
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
            # 压缩后的空 tombstone 使用 offset=length=0；它不再拥有
            # payload，但 RID 仍然由该目录项保留。其他槽必须落在记录区。
            if length == 0 and (slot_flags & SLOT_DELETED) and offset == 0:
                slots.append(Slot(offset, length, slot_flags, slot_reserved))
                continue
            if offset < free_end or offset + length > payload_limit:
                raise _error(
                    StorageFormatError,
                    "SLOT_OUT_OF_BOUNDS",
                    f"slot {slot_id} 记录范围非法：offset={offset}, length={length}, free_end={free_end}",
                    page_id,
                    slot_id=slot_id,
                    offset=slot_offset,
                )
            slots.append(Slot(offset, length, slot_flags, slot_reserved))
            # 被删除槽的旧 payload 可以作为 C-E01 的洞，与新存活槽
            # 暂时重叠；只有存活记录之间的重叠才是格式错误。
            if length and not (slot_flags & SLOT_DELETED):
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
        """由空页构造调用；在写入 struct 前校验各标识字段可表示范围。"""

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
        if version not in SUPPORTED_DATA_PAGE_VERSIONS:
            raise ValueError(f"只支持数据页版本 {SUPPORTED_DATA_PAGE_VERSIONS}")

    @property
    def page_id(self) -> int:
        """供 RID 和磁盘路径读取页号；返回创建或解码得到的固定标识。"""

        return self._page_id

    @property
    def table_id(self) -> int:
        """供 TableHeap 验证归属；返回页头中的表号。"""

        return self._table_id

    @property
    def next_page_id(self) -> int:
        """供 TableHeap 顺链扫描；返回下一页号或 INVALID_PAGE_ID。"""

        return self._next_page_id

    @next_page_id.setter
    def next_page_id(self, value: int) -> None:
        """供 TableHeap 连接新页；校验页号、更新字段并重写页头。"""

        if value != INVALID_PAGE_ID and (
            not isinstance(value, int) or value <= CATALOG_PAGE_ID or value > MAX_I32
        ):
            raise ValueError("next_page_id 必须为 -1 或大于 1")
        self._next_page_id = value
        self._mark_checksum_dirty()
        self._sync_header()

    @property
    def flags(self) -> int:
        """供格式诊断读取页级标志；不修改底层字节。"""

        return self._flags

    @property
    def version(self) -> int:
        """供序列化和校验选择格式；返回页头版本号。"""

        return self._version

    @property
    def slot_count(self) -> int:
        """供扫描和容量诊断读取槽数；由内存槽列表长度计算。"""

        return len(self._slots)

    @property
    def free_start(self) -> int:
        """供 FreeSpaceMap 计算空间；返回槽目录结束偏移。"""

        return self._free_start

    @property
    def free_end(self) -> int:
        """供 FreeSpaceMap 计算空间；返回连续记录区起始偏移。"""

        return self._free_end

    @property
    def payload_limit(self) -> int:
        """当前页可用于记录 payload 的上界。

        Page 插入、压缩和格式校验共同调用。v1 页没有校验和，记录区可以
        一直延伸到页尾。后续的 v2 页会把
        最后四个字节保留给 CRC；把上界作为页属性可以让复用和压缩逻辑
        使用同一个边界，而不会把格式版本散落在调用方。
        """

        return CHECKSUM_OFFSET if self._version == DATA_PAGE_VERSION_V2 else PAGE_SIZE

    @property
    def payload_end(self) -> int:
        """提供容量算法兼容别名；直接返回 payload_limit。"""

        return self.payload_limit

    @property
    def checksum_offset(self) -> int | None:
        """供诊断定位校验和；v2 返回固定偏移，v1 返回 None。"""

        return CHECKSUM_OFFSET if self._version == DATA_PAGE_VERSION_V2 else None

    @property
    def available(self) -> int:
        """供 TableHeap 判断连续空间；计算 free_end 与 free_start 的非负差。"""

        return max(0, self._free_end - self._free_start)

    @property
    def data(self) -> bytes:
        """供兼容调用读取完整页；通过 to_bytes 同步页头并按版本封装校验和。"""

        return self.to_bytes()

    @property
    def slots(self) -> tuple[Slot, ...]:
        """供扫描和测试读取槽目录；返回不可变快照以保护内部列表。"""

        return tuple(self._slots)

    @property
    def live_count(self) -> int:
        """供统计读取存活记录数；遍历槽并排除 tombstone。"""

        return sum(not slot.is_deleted for slot in self._slots)

    def slot(self, slot_id: int) -> Slot:
        """供 RID 读取和删除定位槽；校验索引范围后返回对应 Slot。"""

        if not isinstance(slot_id, int) or isinstance(slot_id, bool) or not 0 <= slot_id < len(self._slots):
            raise _error(SlotNotFoundError, "SLOT_NOT_FOUND", f"slot_id 不存在：{slot_id}", self._page_id, slot_id=slot_id)
        return self._slots[slot_id]

    get_slot = slot

    def insert(self, payload: bytes | bytearray | memoryview) -> int:
        """供 TableHeap 插入二进制行；委托可复用洞的实现并返回新 slot_id。"""

        return self.insert_reusing_hole(payload)

    def find_hole(self, size: int) -> int | None:
        """按 first-fit 返回一个可容纳 ``size`` 字节的已删除 payload 洞。

        insert_reusing_hole 在追加记录前调用。删除槽本身仍然占用目录项，
        新的记录只能追加新的 Slot。候选洞先
        从删除槽的 payload 区收集，再减去所有存活记录，因而连续插入不会
        覆盖刚刚写入的存活记录。返回值是洞的起始偏移，找不到时为 None。
        """

        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("洞大小必须是非负整数")
        if size == 0:
            return self._free_end
        self.validate()
        deleted_ranges = [
            (slot.offset, slot.offset + slot.length)
            for slot in self._slots
            if slot.is_deleted and slot.length > 0
        ]
        if not deleted_ranges:
            return None
        # 先合并删除槽的相邻区间，再从中扣除存活 payload。
        merged: list[tuple[int, int]] = []
        for start, end in sorted(deleted_ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        live_ranges = sorted(
            (slot.offset, slot.offset + slot.length)
            for slot in self._slots
            if not slot.is_deleted and slot.length > 0
        )
        available_ranges = merged
        for live_start, live_end in live_ranges:
            next_ranges: list[tuple[int, int]] = []
            for start, end in available_ranges:
                if live_end <= start or live_start >= end:
                    next_ranges.append((start, end))
                    continue
                if start < live_start:
                    next_ranges.append((start, live_start))
                if live_end < end:
                    next_ranges.append((live_end, end))
            available_ranges = next_ranges
        for start, end in sorted(available_ranges):
            if end - start >= size:
                return start
        return None

    def insert_reusing_hole(self, payload: bytes | bytearray | memoryview) -> int:
        """插入记录，优先复用删除 payload 洞但始终追加新的 Slot。

        TableHeap 调用它落盘编码行。该方法只在完成所有容量和页格式检查后
        才修改字节，因此失败路径
        保证页内容与 Slot 目录保持不变。洞不足时回退到页尾的普通追加。
        """

        raw_payload = bytes(payload)
        required = SLOT_SIZE + len(raw_payload)
        # 新 Slot 的目录空间无论 payload 放在哪里都必须位于记录区之前。
        directory_end = self._free_start + SLOT_SIZE
        available = self._free_end - self._free_start
        if directory_end > self._free_end:
            raise _error(
                PageFullError,
                "PAGE_FULL",
                f"页没有新的 Slot 目录空间：需要 {SLOT_SIZE} 字节，剩余 {available} 字节",
                self._page_id,
                payload_length=len(raw_payload),
                available=available,
                directory_end=directory_end,
            )
        hole_offset = self.find_hole(len(raw_payload)) if raw_payload else None
        if hole_offset is not None:
            self._mark_checksum_dirty()
            slot_id = len(self._slots)
            slot = Slot(hole_offset, len(raw_payload), 0, 0)
            # find_hole 已经完成验证；单次写入不可能覆盖存活记录。
            self._data[hole_offset : hole_offset + len(raw_payload)] = raw_payload
            SLOT_STRUCT.pack_into(
                self._data,
                self._free_start,
                slot.offset,
                slot.length,
                slot.flags,
                slot.reserved,
            )
            self._slots.append(slot)
            self._free_start = directory_end
            self._sync_header()
            return slot_id
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
        self._mark_checksum_dirty()
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

    def compact(self) -> None:
        """压缩页内存活 payload，保持 slot_id/RID 稳定。

        页面维护或扩展任务调用。先从当前字节构造只读快照并完整校验，再
        在临时页缓冲中按 slot_id
        复制存活记录。任何校验失败都在修改前抛出，因而原页字节保持不变。
        """

        raw_before = bytes(self._data)
        if self._version == DATA_PAGE_VERSION_V2:
            from .checksum import crc32

            stored_crc = int.from_bytes(raw_before[CHECKSUM_OFFSET : CHECKSUM_OFFSET + 4], "little")
            if stored_crc and stored_crc != crc32(raw_before):
                # 已从磁盘加载的页若被直接篡改，不能通过重新 seal 掩盖损坏。
                from .checksum import verify_page

                verify_page(raw_before, page_id=self._page_id)
            raw_before = self.to_bytes()
        parsed = type(self).from_bytes(raw_before, expected_page_id=self._page_id)
        live_payloads = {
            slot_id: bytes(parsed._data[slot.offset : slot.offset + slot.length])
            for slot_id, slot in enumerate(parsed._slots)
            if not slot.is_deleted
        }
        new_data = bytearray(PAGE_SIZE)
        cursor = parsed.payload_limit
        new_slots: list[Slot] = []
        for slot_id, slot in enumerate(parsed._slots):
            if slot.is_deleted:
                new_slots.append(Slot(0, 0, slot.flags | SLOT_DELETED, slot.reserved))
                continue
            payload = live_payloads[slot_id]
            cursor -= len(payload)
            new_data[cursor : cursor + len(payload)] = payload
            new_slots.append(Slot(cursor, len(payload), slot.flags, slot.reserved))
        free_start = DATA_PAGE_HEADER_SIZE + len(new_slots) * SLOT_SIZE
        if free_start > cursor:
            # 该情况理论上已被 from_bytes 的边界校验排除；保留明确错误
            # 以防未来格式版本改变目录大小。
            raise _error(
                StorageFormatError,
                "COMPACT_OVERLAP",
                "压缩后的 Slot 目录与 payload 区重叠",
                self._page_id,
                free_start=free_start,
                free_end=cursor,
            )
        for slot_id, slot in enumerate(new_slots):
            SLOT_STRUCT.pack_into(
                new_data,
                DATA_PAGE_HEADER_SIZE + slot_id * SLOT_SIZE,
                slot.offset,
                slot.length,
                slot.flags,
                slot.reserved,
            )
        self._data = new_data
        self._page_id = parsed._page_id
        self._table_id = parsed._table_id
        self._next_page_id = parsed._next_page_id
        self._flags = parsed._flags
        self._version = parsed._version
        self._slots = new_slots
        self._free_start = free_start
        self._free_end = cursor
        self._sync_header()

    compact_page = compact
    def compact_in_place(self) -> None:
        """供偏好显式原地语义的调用方使用；委托 compact 保持 RID 稳定。"""

        self.compact()

    def read(self, slot_id: int) -> bytes:
        """供 TableHeap 按 RID 取行；定位槽、拒绝 tombstone 后复制 payload。"""

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
        """供 DELETE 执行路径标记记录；幂等设置删除位并同步槽目录字节。"""

        slot = self.slot(slot_id)
        if slot.is_deleted:
            return False
        self._mark_checksum_dirty()
        updated = Slot(slot.offset, slot.length, slot.flags | SLOT_DELETED, slot.reserved)
        self._slots[slot_id] = updated
        SLOT_STRUCT.pack_into(self._data, DATA_PAGE_HEADER_SIZE + slot_id * SLOT_SIZE, updated.offset, updated.length, updated.flags, updated.reserved)
        return True

    delete = mark_deleted
    delete_slot = mark_deleted

    def iter_live(self) -> Iterator[tuple[int, bytes]]:
        """供 TableHeap 顺序扫描；按 slot_id 产生未删除记录的字节副本。"""

        for slot_id, slot in enumerate(self._slots):
            if not slot.is_deleted:
                yield slot_id, bytes(self._data[slot.offset : slot.offset + slot.length])

    def validate(self) -> None:
        """供写回前自检；序列化后重新解析，并比较槽状态是否一致。"""

        parsed = type(self).from_bytes(self.to_bytes(), expected_page_id=self._page_id)
        if parsed._slots != self._slots:
            raise _error(StorageFormatError, "PAGE_STATE_MISMATCH", "页内状态与序列化字节不一致", self._page_id)

    def to_bytes(self) -> bytes:
        """供 BufferPool/DiskManager 写回；同步页头，v2 额外写入 CRC。"""

        self._sync_header()
        raw = bytes(self._data)
        if self._version == DATA_PAGE_VERSION_V2:
            from .checksum import seal_page

            return seal_page(raw)
        return raw

    serialize = to_bytes

    def _sync_header(self) -> None:
        """由所有页状态变更调用；清旧校验和并把内存字段打包到固定页头。"""

        self._mark_checksum_dirty()
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

    def _mark_checksum_dirty(self) -> None:
        """由页修改路径调用；v2 把 CRC 字段清零，等待 to_bytes 重新封装。"""

        if self._version == DATA_PAGE_VERSION_V2:
            self._data[CHECKSUM_OFFSET : CHECKSUM_OFFSET + 4] = bytes(4)


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
