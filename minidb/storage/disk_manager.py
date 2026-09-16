"""MiniDB 固定大小页的磁盘管理器和持久化空闲页链。

``DatabaseFile`` 负责 page 0/page 1 的启动格式；本模块在其上提供页号
定位、追加、释放和重启后恢复空闲链。模块不持有缓冲池，释放时通过一个
可选的鸭子类型协调器让上层先失效缓存帧，避免旧 dirty 数据覆盖新页。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Final

from minidb.contracts.errors import StorageError, StorageIOError

from .constants import (
    CATALOG_PAGE_ID,
    DATA_PAGE_HEADER_SIZE,
    DATA_PAGE_MAGIC,
    DATA_PAGE_STRUCT,
    DATA_PAGE_VERSION,
    DATA_PAGE_VERSION_V2,
    FORMAT_VERSION_V2,
    INVALID_PAGE_ID,
    MAX_I32,
    PAGE_SIZE,
)
from .superblock import DatabaseFile, StorageFormatError


FREE_PAGE_MAGIC: Final[bytes] = b"FREE"
FREE_PAGE_STRUCT = DATA_PAGE_STRUCT
FREE_PAGE_FORMAT: Final[str] = DATA_PAGE_STRUCT.format


def _storage_error(code: str, message: str, **context: object) -> StorageError:
    """供页分配与释放分支创建逻辑错误；统一附带无 SQL span 的上下文。"""

    return StorageError(code, message, span=None, context=context)


def _io_error(code: str, message: str, **context: object) -> StorageIOError:
    """供磁盘读写和关闭状态分支创建 I/O 错误；保留路径及偏移信息。"""

    return StorageIOError(code, message, span=None, context=context)


class DiskManager:
    """连接 BufferPool 与 DatabaseFile，按页读写并持久化空闲页链。"""

    def __init__(
        self,
        database: DatabaseFile | str | Path,
        *,
        owns_database: bool | None = None,
        cache_coordinator: object | None = None,
        format_version: int | None = None,
        version: int | None = None,
    ) -> None:
        """由存储装配层创建；接收或打开数据库，并从 superblock 恢复空闲链。"""

        if format_version is None:
            format_version = version
        if isinstance(database, DatabaseFile):
            self._database = database
            self._owns_database = bool(owns_database) if owns_database is not None else False
        else:
            if format_version == FORMAT_VERSION_V2:
                self._database = DatabaseFile.open_v2(database)
            else:
                self._database = DatabaseFile.open(database)
            self._owns_database = True if owns_database is None else bool(owns_database)
        self._cache_coordinator = cache_coordinator
        self._closed = False
        self._free_pages: set[int] = set()
        try:
            self._load_free_list()
        except StorageError:
            if self._owns_database:
                try:
                    self._database.close()
                except StorageError as close_error:
                    _ = close_error
            raise

    @classmethod
    def open(cls, path: str | Path, **kwargs: object) -> "DiskManager":
        """供普通启动流程打开数据库；把路径和选项转交构造器。"""

        return cls(path, **kwargs)

    @classmethod
    def open_v2(cls, path: str | Path, **kwargs: object) -> "DiskManager":
        """供校验和格式实验打开数据库；固定 v2 格式后调用构造器。"""

        kwargs["format_version"] = FORMAT_VERSION_V2
        return cls(path, **kwargs)

    @classmethod
    def initialize_v2(cls, path: str | Path, **kwargs: object) -> "DiskManager":
        """供显式创建 v2 数据库；先初始化 DatabaseFile，再取得其关闭所有权。"""

        from .superblock import DatabaseFile

        database = DatabaseFile.initialize_v2(path)
        kwargs["owns_database"] = True
        return cls(database, **kwargs)

    @property
    def database(self) -> DatabaseFile:
        """供底层维护和测试读取 DatabaseFile；不改变句柄所有权。"""

        return self._database

    @property
    def path(self) -> Path:
        """供错误与诊断显示文件位置；转发 DatabaseFile.path。"""

        return self._database.path

    @property
    def page_count(self) -> int:
        """供边界检查和分配读取总页数；转发 superblock 中的持久化值。"""

        return self._database.page_count

    @property
    def superblock(self):
        """供存储装配读取只读元数据；返回当前 DatabaseFile superblock。"""

        return self._database.superblock

    @property
    def free_head(self) -> int:
        """供诊断读取空闲链头；转发当前 superblock.free_head。"""

        return self._database.superblock.free_head

    @property
    def next_table_id(self) -> int:
        """供最终装配注入 CatalogService；读取持久化表号分配下界。"""

        return self._database.next_table_id

    @property
    def page_version(self) -> int:
        """供新页和 FREE 页编码选择版本；转发数据库页格式版本。"""

        return self._database.page_version

    @property
    def format_version(self) -> int:
        """提供 page_version 的兼容名称；供配置和测试读取。"""

        return self.page_version

    @property
    def free_pages(self) -> frozenset[int]:
        """供诊断读取已验证空闲页集合；返回不可变副本。"""

        return frozenset(self._free_pages)

    @property
    def free_list(self) -> tuple[int, ...]:
        """供诊断按链序读取空闲页；委托 free_list_chain 重新验证链。"""

        return self.free_list_chain()

    @property
    def closed(self) -> bool:
        """供每次 I/O 前检查状态；合并管理器与底层数据库关闭标记。"""

        return self._closed or self._database.closed

    def attach_cache_coordinator(self, coordinator: object | None) -> None:
        """由 BufferPool 初始化调用；登记释放页前需检查和失效的缓存对象。"""

        self._cache_coordinator = coordinator

    def read_page(self, page_id: int) -> bytes:
        """供 BufferPool 读取整页；检查状态与范围，再把短读转换为 I/O 错误。"""

        self._check_open()
        self._check_page_id(page_id)
        try:
            return self._database.read_page(page_id)
        except StorageFormatError as error:
            if error.code == "SHORT_READ":
                raise _io_error(
                    "READ_PAGE_FAILED",
                    f"读取 page {page_id} 时发生短读：{error.message}",
                    page_id=page_id,
                    offset=page_id * PAGE_SIZE,
                ) from error
            raise

    read = read_page

    def write_page(self, page_id: int, data: bytes | bytearray | memoryview) -> None:
        """供 BufferPool 写回整页；校验状态、页号和固定长度后交给 DatabaseFile。"""

        self._check_open()
        self._check_page_id(page_id)
        raw = bytes(data)
        if len(raw) != PAGE_SIZE:
            raise ValueError(f"页数据必须恰好为 {PAGE_SIZE} 字节，实际为 {len(raw)}")
        self._database.write_page(page_id, raw)

    write = write_page

    def allocate_page(self) -> int:
        """供 TableHeap 分配页；优先弹出空闲链头并持久化，否则在文件尾追加空页。"""

        self._check_open()
        head = self._database.superblock.free_head
        if head != INVALID_PAGE_ID:
            if head not in self._free_pages:
                raise StorageFormatError(
                    "FREE_LIST_STATE_MISMATCH",
                    f"free_head {head} 不在已校验空闲链中",
                    {"field": "free_head", "page_id": head},
                )
            raw = self.read_page(head)
            next_free = self._decode_free_page(raw, expected_page_id=head)
            current = self._database.superblock
            self._database.write_page(head, self._blank_page(head))
            self._database.persist_superblock(current.__class__(
                current.version,
                current.page_size,
                current.page_count,
                next_free,
                current.catalog_head,
                current.next_table_id,
            ))
            self._free_pages.remove(head)
            return head
        if self.page_count > MAX_I32:
            raise _storage_error(
                "PAGE_ID_EXHAUSTED",
                f"无法分配 page_id：page_count={self.page_count} 已达到有符号 32 位上限",
                page_count=self.page_count,
            )
        return self._database.append_page(self._blank_page(self.page_count))

    allocate = allocate_page

    def free_page(
        self,
        page_id: int,
        *,
        cache_coordinator: object | None = None,
        coordinator: object | None = None,
        live: bool = False,
        linked: bool = False,
    ) -> None:
        """供 TableHeap 回收空页；检查占用与链关系、失效缓存，再压入持久化空闲链。"""

        self._check_open()
        self._check_page_id(page_id)
        if page_id <= CATALOG_PAGE_ID:
            raise _storage_error(
                "PROTECTED_PAGE",
                f"page {page_id} 是 Superblock 或 Catalog 保留页，不能释放",
                page_id=page_id,
            )
        if page_id in self._free_pages:
            raise _storage_error("DUPLICATE_FREE", f"page {page_id} 已在空闲链中", page_id=page_id)
        coordinator = (
            cache_coordinator
            if cache_coordinator is not None
            else coordinator if coordinator is not None else self._cache_coordinator
        )
        if live or linked:
            raise _storage_error(
                "PAGE_STILL_IN_USE",
                f"page {page_id} 仍有存活记录或属于表链，不能释放",
                page_id=page_id,
                live=live,
                linked=linked,
            )
        self._reject_coordinator_in_use(coordinator, page_id)
        raw_before = self.read_page(page_id)
        if self._looks_like_live_data_page(raw_before, page_id):
            raise _storage_error(
                "PAGE_HAS_RECORDS",
                f"page {page_id} 的页头仍声明存在记录，不能释放",
                page_id=page_id,
            )
        if self._looks_like_linked_data_page(raw_before, page_id):
            raise _storage_error(
                "PAGE_STILL_LINKED",
                f"page {page_id} 的数据页头仍连接着后继页，不能释放",
                page_id=page_id,
            )
        if coordinator is not None:
            self._invalidate_coordinator(coordinator, page_id)
        next_free = self._database.superblock.free_head
        free_page = self._encode_free_page(page_id, next_free, version=self.page_version)
        try:
            self._database.write_page(page_id, free_page)
            current = self._database.superblock
            self._database.persist_superblock(current.__class__(
                current.version,
                current.page_size,
                current.page_count,
                page_id,
                current.catalog_head,
                current.next_table_id,
            ))
        except StorageError:
            self._restore_page_after_failed_free(page_id, raw_before)
            raise
        self._free_pages.add(page_id)

    free = free_page

    def update_next_table_id(self, next_table_id: int) -> int:
        """供 Catalog 创建表前推进分配下界；只允许递增并重写 superblock。"""

        self._check_open()
        current = self._database.superblock.next_table_id
        if not isinstance(next_table_id, int) or isinstance(next_table_id, bool):
            raise ValueError("next_table_id 必须是整数")
        if next_table_id < current:
            raise ValueError(f"next_table_id 只能前进：当前为 {current}，请求为 {next_table_id}")
        if next_table_id == current:
            return current
        current_sb = self._database.superblock
        self._database.persist_superblock(current_sb.__class__(
            current_sb.version,
            current_sb.page_size,
            current_sb.page_count,
            current_sb.free_head,
            current_sb.catalog_head,
            next_table_id,
        ))
        return next_table_id

    def free_list_chain(self) -> tuple[int, ...]:
        """供启动检查和诊断只读遍历空闲链；逐页解码并检测环与坏指针。"""

        chain: list[int] = []
        seen: set[int] = set()
        page_id = self._database.superblock.free_head
        while page_id != INVALID_PAGE_ID:
            if page_id in seen:
                raise StorageFormatError(
                    "FREE_LIST_CYCLE",
                    f"空闲链在 page {page_id} 形成环",
                    {"page_id": page_id},
                )
            seen.add(page_id)
            chain.append(page_id)
            page_id = self._decode_free_page(self.read_page(page_id), expected_page_id=page_id)
        return tuple(chain)

    def flush(self) -> None:
        """供 BufferPool.flush_all 调用；检查开启状态后刷新底层文件句柄。"""

        self._check_open()
        self._database.flush()

    flush_all = flush

    def close(self) -> None:
        """供上下文退出或显式关闭；按所有权关闭 DatabaseFile 并保持幂等。"""

        if self._closed:
            return
        if self._owns_database:
            self._database.close()
        self._closed = True

    def __enter__(self) -> "DiskManager":
        """支持 ``with DiskManager``；确认可用后返回自身。"""

        self._check_open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """由上下文管理器调用；退出时统一执行 close。"""

        self.close()

    def _load_free_list(self) -> None:
        """由构造器调用；从 free_head 逐页解码、验证边界并建立内存集合。"""

        page_id = self._database.superblock.free_head
        seen: set[int] = set()
        while page_id != INVALID_PAGE_ID:
            if page_id in seen:
                raise StorageFormatError(
                    "FREE_LIST_CYCLE",
                    f"空闲链在 page {page_id} 形成环",
                    {"page_id": page_id},
                )
            if page_id <= CATALOG_PAGE_ID or page_id >= self.page_count:
                raise StorageFormatError(
                    "BAD_FREE_PAGE_ID",
                    f"空闲链页号越界：{page_id}",
                    {"page_id": page_id, "page_count": self.page_count},
                )
            seen.add(page_id)
            self._free_pages.add(page_id)
            page_id = self._decode_free_page(self.read_page(page_id), expected_page_id=page_id)

    def _decode_free_page(self, raw: bytes, *, expected_page_id: int) -> int:
        """由空闲链遍历调用；校验 FREE 页全部固定字段并返回后继页号。"""

        if len(raw) != PAGE_SIZE:
            raise StorageFormatError(
                "TRUNCATED_FREE_PAGE",
                f"FREE page 必须为 {PAGE_SIZE} 字节，实际为 {len(raw)}",
                {"page_id": expected_page_id, "offset": expected_page_id * PAGE_SIZE},
            )
        try:
            magic, page_id, next_page_id, slot_count, free_start, free_end, flags, table_id, version, reserved = (
                FREE_PAGE_STRUCT.unpack_from(raw, 0)
            )
        except struct.error as error:
            raise StorageFormatError(
                "INVALID_FREE_PAGE",
                f"FREE page 头解码失败：{error}",
                {"page_id": expected_page_id, "offset": expected_page_id * PAGE_SIZE},
            ) from error
        if magic != FREE_PAGE_MAGIC:
            raise StorageFormatError(
                "BAD_FREE_MAGIC",
                f"空闲页 magic 错误：实际为 {magic!r}",
                {"page_id": expected_page_id, "field": "magic", "offset": expected_page_id * PAGE_SIZE},
            )
        if page_id != expected_page_id:
            raise StorageFormatError(
                "BAD_FREE_PAGE_ID",
                f"FREE page_id 错误：期望 {expected_page_id}，实际 {page_id}",
                {"page_id": expected_page_id, "field": "page_id", "offset": expected_page_id * PAGE_SIZE + 4},
            )
        if next_page_id != INVALID_PAGE_ID and not CATALOG_PAGE_ID < next_page_id < self.page_count:
            raise StorageFormatError(
                "BAD_FREE_NEXT",
                f"FREE next_page_id 越界：{next_page_id}",
                {"page_id": expected_page_id, "field": "next_page_id", "offset": expected_page_id * PAGE_SIZE + 8},
            )
        expected_version = DATA_PAGE_VERSION_V2 if self.page_version == FORMAT_VERSION_V2 else 0
        if (slot_count, free_start, free_end, flags, table_id, version, reserved) != (
            0,
            0,
            0,
            0,
            0,
            expected_version,
            0,
        ):
            raise StorageFormatError(
                "NONZERO_FREE_FIELDS",
                "FREE 页除 page_id 和 next_page_id 外的字段必须为零",
                {"page_id": expected_page_id, "offset": expected_page_id * PAGE_SIZE + 12},
            )
        payload_end = PAGE_SIZE - 4 if self.page_version == FORMAT_VERSION_V2 else PAGE_SIZE
        if any(raw[DATA_PAGE_STRUCT.size:payload_end]):
            raise StorageFormatError(
                "NONZERO_FREE_PAYLOAD",
                "FREE 页剩余区域必须为零",
                {"page_id": expected_page_id, "offset": expected_page_id * PAGE_SIZE + DATA_PAGE_STRUCT.size},
            )
        return next_page_id

    @staticmethod
    def _encode_free_page(page_id: int, next_page_id: int, *, version: int = DATA_PAGE_VERSION) -> bytes:
        """由 free_page 调用；校验链指针、打包固定页头并按 v2 需要封装 CRC。"""

        if page_id <= CATALOG_PAGE_ID or page_id > MAX_I32:
            raise ValueError("可释放 page_id 必须大于 1 且适合有符号 32 位字段")
        if next_page_id != INVALID_PAGE_ID and next_page_id <= CATALOG_PAGE_ID:
            raise ValueError("空闲链后继必须为 -1 或大于 1")
        data_version = DATA_PAGE_VERSION_V2 if version == FORMAT_VERSION_V2 else 0
        header = FREE_PAGE_STRUCT.pack(
            FREE_PAGE_MAGIC,
            page_id,
            next_page_id,
            0,
            0,
            0,
            0,
            0,
            data_version,
            0,
        )
        raw = header + bytes(PAGE_SIZE - FREE_PAGE_STRUCT.size)
        if version == FORMAT_VERSION_V2:
            from .checksum import seal_page

            return seal_page(raw)
        return raw

    def _blank_page(self, page_id: int) -> bytes:
        """由 allocate_page 调用生成可读空页；v1 清零，v2 写页头并封装 CRC。"""

        if self.page_version == FORMAT_VERSION_V2:
            from .checksum import seal_page
            from .superblock import encode_data_page_header

            raw = encode_data_page_header(
                page_id=page_id,
                version=DATA_PAGE_VERSION_V2,
            ) + bytes(PAGE_SIZE - DATA_PAGE_HEADER_SIZE)
            return seal_page(raw)
        return bytes(PAGE_SIZE)

    @staticmethod
    def _looks_like_live_data_page(raw: bytes, page_id: int) -> bool:
        """由 free_page 防误释放调用；宽容解包页头并判断是否声明有槽。"""

        if len(raw) < DATA_PAGE_STRUCT.size:
            return False
        try:
            magic, stored_page_id, _next, slot_count, _start, _end, _flags, _table, _version, _reserved = (
                DATA_PAGE_STRUCT.unpack_from(raw, 0)
            )
        except struct.error:
            return False
        return magic == DATA_PAGE_MAGIC and stored_page_id == page_id and slot_count > 0

    @staticmethod
    def _looks_like_linked_data_page(raw: bytes, page_id: int) -> bool:
        """由 free_page 防断链调用；宽容解包页头并判断后继指针是否存在。"""

        if len(raw) < DATA_PAGE_STRUCT.size:
            return False
        try:
            magic, stored_page_id, next_page_id, _slot_count, _start, _end, _flags, _table, _version, _reserved = (
                DATA_PAGE_STRUCT.unpack_from(raw, 0)
            )
        except struct.error:
            return False
        return magic == DATA_PAGE_MAGIC and stored_page_id == page_id and next_page_id != INVALID_PAGE_ID

    @staticmethod
    def _reject_coordinator_in_use(coordinator: object | None, page_id: int) -> None:
        """由 free_page 调用；通过鸭子类型检查缓存 pin、存活和链接状态。"""

        if coordinator is None:
            return
        pin_count = None
        for name in ("pin_count", "get_pin_count"):
            method = getattr(coordinator, name, None)
            if callable(method):
                pin_count = method(page_id)
                break
        if pin_count is None:
            pinned = getattr(coordinator, "is_pinned", None)
            if callable(pinned):
                pin_count = 1 if pinned(page_id) else 0
        if pin_count is None:
            get_frame = getattr(coordinator, "get_frame", None)
            if callable(get_frame):
                frame = get_frame(page_id)
                pin_count = getattr(frame, "pin_count", 0) if frame is not None else 0
        if pin_count:
            raise _storage_error(
                "PAGE_PINNED",
                f"page {page_id} 仍被缓存 pin，不能释放",
                page_id=page_id,
                pin_count=pin_count,
            )
        for name, flag_name in (
            ("is_live", "live"),
            ("is_live_page", "live"),
            ("is_linked", "linked"),
            ("is_linked_page", "linked"),
        ):
            method = getattr(coordinator, name, None)
            if callable(method) and method(page_id):
                raise _storage_error(
                    "PAGE_STILL_IN_USE",
                    f"page {page_id} 仍被协调器标记为 {flag_name}",
                    page_id=page_id,
                    **{flag_name: True},
                )

    @staticmethod
    def _invalidate_coordinator(coordinator: object, page_id: int) -> None:
        """由 free_page 写 FREE 页前调用；寻找兼容失效方法并丢弃缓存帧。"""

        for name in ("invalidate", "invalidate_page", "discard"):
            method = getattr(coordinator, name, None)
            if callable(method):
                method(page_id)
                return
        raise _storage_error(
            "CACHE_COORDINATOR_INVALID",
            "缓存协调器没有 invalidate(page_id) 接口",
            page_id=page_id,
        )

    def _restore_page_after_failed_free(self, page_id: int, raw: bytes) -> None:
        """由 free_page 异常补偿调用；尽力恢复原页且不掩盖首次存储错误。"""

        try:
            self._database.write_page(page_id, raw)
        except StorageError:
            return

    def _check_open(self) -> None:
        """由所有 I/O 入口调用；文件已关闭时抛出带路径的错误。"""

        if self.closed:
            raise _io_error("CLOSED_DATABASE", "数据库文件已经关闭", path=str(self.path))

    def _check_page_id(self, page_id: int) -> None:
        """由页读写和释放入口调用；校验整数类型及当前 page_count 范围。"""

        if not isinstance(page_id, int) or isinstance(page_id, bool):
            raise ValueError("page_id 必须是整数")
        if not 0 <= page_id < self.page_count:
            raise StorageError(
                "PAGE_OUT_OF_RANGE",
                f"page_id 超出范围：{page_id}",
                span=None,
                context={"page_id": page_id, "page_count": self.page_count, "offset": page_id * PAGE_SIZE},
            )


PageFile = DiskManager


__all__ = [
    "DiskManager",
    "FREE_PAGE_FORMAT",
    "FREE_PAGE_MAGIC",
    "FREE_PAGE_STRUCT",
    "PageFile",
    "StorageFormatError",
]
