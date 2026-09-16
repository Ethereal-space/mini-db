"""Superblock 编解码与 MiniDB 文件初始化，对应任务 C-B01。

本模块只负责数据库文件的页 0/page 1 引导格式与完整性检查。页分配、
空闲链维护、缓冲池和表堆由后续 C 任务实现；这里提供的 ``read_page``
和 ``save_superblock`` 仅用于可靠地读取/保存引导页。
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Self

from minidb.contracts.errors import StorageError, StorageIOError

from .constants import (
    CATALOG_PAGE_ID,
    CHECKSUM_OFFSET,
    CHECKSUM_SIZE,
    DATA_PAGE_HEADER_SIZE,
    DATA_PAGE_MAGIC,
    DATA_PAGE_STRUCT,
    DATA_PAGE_VERSION,
    DATA_PAGE_VERSION_V2,
    FORMAT_VERSION,
    FORMAT_VERSION_V2,
    INITIAL_CATALOG_HEAD,
    INITIAL_FREE_HEAD,
    INITIAL_NEXT_TABLE_ID,
    INITIAL_PAGE_COUNT,
    INVALID_PAGE_ID,
    MAX_I32,
    MAX_U32,
    PAGE_SIZE,
    SUPPORTED_FORMAT_VERSIONS,
    SUPERBLOCK_FORMAT,
    SUPERBLOCK_MAGIC,
    SUPERBLOCK_PAGE_ID,
    SUPERBLOCK_SIZE,
    SUPERBLOCK_STRUCT,
)


class StorageFormatError(StorageError):
    """由引导页解码与文件校验抛出，表示磁盘格式不满足 MiniDB 约束。"""

    def __init__(self, code: str, message: str, context: dict[str, object] | None = None) -> None:
        """由格式检查分支调用；固定 span=None，并把字段和偏移作为上下文。"""

        super().__init__(code, message, span=None, context=context)


def _format_error(code: str, message: str, **context: object) -> StorageFormatError:
    """供本模块格式检查统一创建 StorageFormatError；透传字段与偏移。"""

    return StorageFormatError(code, message, context)


def _io_error(code: str, message: str, **context: object) -> StorageIOError:
    """供文件系统异常分支统一创建 StorageIOError；补齐无 SQL span 的上下文。"""

    return StorageIOError(code, message, span=None, context=context)


def _close_quietly(handle: object) -> None:
    """由打开失败清理路径调用；尽力关闭句柄且不覆盖原始格式错误。"""

    try:
        handle.close()  # type: ignore[union-attr]
    except OSError:
        return


def _check_u32(value: int, field: str) -> None:
    """由字段构造与页头编码调用；确认值可由无符号 32 位整数表示。"""

    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_U32:
        raise ValueError(f"{field} 必须是 0..{MAX_U32} 的整数")


def _check_i32(value: int, field: str) -> None:
    """由字段构造调用；确认值可由有符号 32 位整数表示。"""

    if not isinstance(value, int) or isinstance(value, bool) or not -(1 << 31) <= value <= MAX_I32:
        raise ValueError(f"{field} 必须是有符号 32 位整数")


@dataclass(frozen=True, slots=True)
class Superblock:
    """表示页 0 元数据；由 DatabaseFile 编解码，magic 和页大小由格式固定。"""

    version: int = FORMAT_VERSION
    page_size: int = PAGE_SIZE
    page_count: int = INITIAL_PAGE_COUNT
    free_head: int = INITIAL_FREE_HEAD
    catalog_head: int = INITIAL_CATALOG_HEAD
    next_table_id: int = INITIAL_NEXT_TABLE_ID

    def __post_init__(self) -> None:
        """由 dataclass 构造后自动调用；逐字段检查其 struct 可表示范围。"""

        _check_u32(self.version, "version")
        _check_u32(self.page_size, "page_size")
        _check_u32(self.page_count, "page_count")
        _check_i32(self.free_head, "free_head")
        _check_i32(self.catalog_head, "catalog_head")
        _check_u32(self.next_table_id, "next_table_id")

    @classmethod
    def initial(cls, version: int = FORMAT_VERSION) -> Self:
        """供 DatabaseFile 初始化新库；校验版本后返回固定初始元数据。"""

        if version not in SUPPORTED_FORMAT_VERSIONS:
            raise ValueError(f"不支持的数据库格式版本：{version}")
        return cls(version=version)

    @classmethod
    def initial_v2(cls) -> Self:
        """供 v2 初始化入口使用；委托 initial 构造带校验和的初始元数据。"""

        return cls.initial(FORMAT_VERSION_V2)

    def encode(self) -> bytes:
        """供新库创建和页 0 更新；打包固定字段、补零，v2 再封装 CRC。"""

        try:
            header = SUPERBLOCK_STRUCT.pack(
                SUPERBLOCK_MAGIC,
                self.version,
                self.page_size,
                self.page_count,
                self.free_head,
                self.catalog_head,
                self.next_table_id,
            )
        except struct.error as error:
            raise ValueError(f"Superblock 字段无法编码：{error}") from error
        raw = bytearray(header + bytes(PAGE_SIZE - SUPERBLOCK_SIZE))
        if self.version == FORMAT_VERSION_V2:
            from .checksum import seal_page

            return seal_page(raw)
        return bytes(raw)

    @classmethod
    def decode(
        cls,
        data: bytes | bytearray | memoryview,
        *,
        allow_versions: tuple[int, ...] | None = None,
        supported_versions: tuple[int, ...] | None = None,
        verify_checksum: bool = True,
    ) -> Self:
        """供 DatabaseFile 打开页 0；依次校验长度、字段、版本、CRC 和边界且不写文件。"""

        raw = bytes(data)
        if len(raw) < SUPERBLOCK_SIZE:
            raise _format_error(
                "TRUNCATED_SUPERBLOCK",
                f"Superblock 至少需要 {SUPERBLOCK_SIZE} 字节，实际为 {len(raw)} 字节",
                offset=0,
                actual_length=len(raw),
            )
        try:
            magic, version, page_size, page_count, free_head, catalog_head, next_table_id = (
                SUPERBLOCK_STRUCT.unpack_from(raw, 0)
            )
        except struct.error as error:
            raise _format_error("INVALID_SUPERBLOCK", f"Superblock 解码失败：{error}", offset=0) from error
        if magic != SUPERBLOCK_MAGIC:
            raise _format_error(
                "BAD_MAGIC",
                f"Superblock magic 错误：期望 {SUPERBLOCK_MAGIC!r}，实际 {magic!r}",
                field="magic",
                offset=0,
            )
        if allow_versions is not None and supported_versions is not None:
            raise ValueError("allow_versions 与 supported_versions 不能同时提供")
        selected_versions = allow_versions if allow_versions is not None else supported_versions
        supported = (FORMAT_VERSION,) if selected_versions is None else tuple(selected_versions)
        if version not in supported:
            raise _format_error(
                "UNSUPPORTED_VERSION",
                f"不支持的 Superblock version：{version}，允许 {supported}",
                field="version",
                offset=8,
            )
        if version == FORMAT_VERSION_V2 and verify_checksum:
            from .checksum import ChecksumError, verify_page

            try:
                verify_page(raw, page_id=SUPERBLOCK_PAGE_ID)
            except ValueError as error:
                raise _format_error(
                    "TRUNCATED_SUPERBLOCK",
                    f"v2 Superblock 校验需要完整 {PAGE_SIZE} 字节：{error}",
                    offset=0,
                    actual_length=len(raw),
                ) from error
            except ChecksumError:
                raise
        if len(raw) >= PAGE_SIZE:
            reserved_end = CHECKSUM_OFFSET if version == FORMAT_VERSION_V2 else PAGE_SIZE
            if any(raw[SUPERBLOCK_SIZE:reserved_end]):
                raise _format_error(
                    "NONZERO_SUPERBLOCK_RESERVED",
                    "Superblock 保留区必须全部为零",
                    offset=SUPERBLOCK_SIZE,
                )
        if page_size != PAGE_SIZE:
            raise _format_error(
                "BAD_PAGE_SIZE",
                f"页大小错误：{page_size}，期望 {PAGE_SIZE}",
                field="page_size",
                offset=12,
            )
        if page_count < INITIAL_PAGE_COUNT:
            raise _format_error(
                "BAD_PAGE_COUNT",
                f"page_count 必须至少为 {INITIAL_PAGE_COUNT}，实际为 {page_count}",
                field="page_count",
                offset=16,
            )
        if free_head != INVALID_PAGE_ID and not CATALOG_PAGE_ID < free_head < page_count:
            raise _format_error(
                "BAD_FREE_HEAD",
                f"free_head 超出页号范围：{free_head}",
                field="free_head",
                offset=20,
                page_count=page_count,
            )
        if catalog_head != CATALOG_PAGE_ID or not 1 <= catalog_head < page_count:
            raise _format_error(
                "BAD_CATALOG_HEAD",
                f"catalog_head 必须指向 page 1，实际为 {catalog_head}",
                field="catalog_head",
                offset=24,
                page_count=page_count,
            )
        if next_table_id < INITIAL_NEXT_TABLE_ID:
            raise _format_error(
                "BAD_NEXT_TABLE_ID",
                f"next_table_id 必须至少为 {INITIAL_NEXT_TABLE_ID}，实际为 {next_table_id}",
                field="next_table_id",
                offset=28,
            )
        return cls(version, page_size, page_count, free_head, catalog_head, next_table_id)

    from_bytes = decode

    def to_bytes(self) -> bytes:
        """提供序列化兼容接口；直接委托 encode 生成完整页。"""

        return self.encode()

    def describe_header(self) -> dict[str, dict[str, int | bytes]]:
        """供演示和诊断返回字段偏移和值；只组装内存字典，不访问文件。"""

        return {
            "magic": {"offset": 0, "value": SUPERBLOCK_MAGIC},
            "version": {"offset": 8, "value": self.version},
            "page_size": {"offset": 12, "value": self.page_size},
            "page_count": {"offset": 16, "value": self.page_count},
            "free_head": {"offset": 20, "value": self.free_head},
            "catalog_head": {"offset": 24, "value": self.catalog_head},
            "next_table_id": {"offset": 28, "value": self.next_table_id},
        }

    def validate_file_size(self, file_size: int) -> None:
        """供打开和保存页 0 前检查完整性；比较实际长度与 page_count×PAGE_SIZE。"""

        expected = self.page_count * PAGE_SIZE
        if file_size != expected:
            raise _format_error(
                "BAD_FILE_LENGTH",
                f"数据库文件长度错误：期望 {expected} 字节，实际 {file_size} 字节",
                field="file_size",
                offset=16,
                page_count=self.page_count,
                expected_length=expected,
                actual_length=file_size,
            )


def encode_data_page_header(
    *,
    page_id: int = CATALOG_PAGE_ID,
    next_page_id: int = INVALID_PAGE_ID,
    slot_count: int = 0,
    free_start: int = DATA_PAGE_HEADER_SIZE,
    free_end: int | None = None,
    flags: int = 0,
    table_id: int = 0,
    version: int = DATA_PAGE_VERSION,
    reserved: int = 0,
) -> bytes:
    """供新库和 v2 空页创建固定数据页头；校验字段后按 struct 打包。"""

    if page_id < 1:
        raise ValueError("数据页 page_id 必须大于或等于 1")
    if next_page_id != INVALID_PAGE_ID and next_page_id < CATALOG_PAGE_ID + 1:
        raise ValueError("next_page_id 必须为 -1 或大于等于 2")
    if not 0 <= slot_count <= 0xFFFF:
        raise ValueError("slot_count 必须适合无符号 16 位字段")
    if free_end is None:
        free_end = CHECKSUM_OFFSET if version == FORMAT_VERSION_V2 else PAGE_SIZE
    if not DATA_PAGE_HEADER_SIZE <= free_start <= free_end <= (
        CHECKSUM_OFFSET if version == FORMAT_VERSION_V2 else PAGE_SIZE
    ):
        raise ValueError("数据页 free_start/free_end 不满足页边界")
    for value, field in ((page_id, "page_id"), (table_id, "table_id"), (version, "version"), (reserved, "reserved")):
        _check_u32(value, field)
    if next_page_id > MAX_I32:
        raise ValueError("next_page_id 必须适合有符号 32 位字段")
    if not 0 <= flags <= 0xFFFF:
        raise ValueError("flags 必须适合无符号 16 位字段")
    return DATA_PAGE_STRUCT.pack(
        DATA_PAGE_MAGIC,
        page_id,
        next_page_id,
        slot_count,
        free_start,
        free_end,
        flags,
        table_id,
        version,
        reserved,
    )


def _validate_catalog_page(
    page: bytes,
    *,
    expected_page_id: int,
    page_count: int | None = None,
    allow_versions: tuple[int, ...] | None = None,
) -> None:
    """由 DatabaseFile 打开已有库时调用；校验 Catalog 页头、版本、CRC 和边界。"""

    if len(page) < DATA_PAGE_HEADER_SIZE:
        raise _format_error(
            "TRUNCATED_CATALOG_PAGE",
            f"Catalog page 至少需要 {DATA_PAGE_HEADER_SIZE} 字节，实际为 {len(page)} 字节",
            page_id=expected_page_id,
            offset=expected_page_id * PAGE_SIZE,
        )
    try:
        magic, page_id, next_page_id, slot_count, free_start, free_end, flags, table_id, version, reserved = (
            DATA_PAGE_STRUCT.unpack_from(page, 0)
        )
    except struct.error as error:
        raise _format_error(
            "INVALID_CATALOG_PAGE",
            f"Catalog page 头解码失败：{error}",
            page_id=expected_page_id,
            offset=expected_page_id * PAGE_SIZE,
        ) from error
    if magic != DATA_PAGE_MAGIC:
        raise _format_error(
            "BAD_CATALOG_MAGIC",
            f"Catalog page magic 错误：期望 {DATA_PAGE_MAGIC!r}，实际 {magic!r}",
            field="magic",
            page_id=expected_page_id,
            offset=expected_page_id * PAGE_SIZE,
        )
    if page_id != expected_page_id:
        raise _format_error(
            "BAD_CATALOG_PAGE_ID",
            f"Catalog page_id 错误：期望 {expected_page_id}，实际 {page_id}",
            field="page_id",
            page_id=expected_page_id,
            offset=expected_page_id * PAGE_SIZE + 4,
        )
    if next_page_id != INVALID_PAGE_ID and (
        next_page_id < CATALOG_PAGE_ID + 1
        or (page_count is not None and next_page_id >= page_count)
    ):
        raise _format_error(
            "BAD_CATALOG_NEXT",
            f"Catalog next_page_id 非法：{next_page_id}",
            field="next_page_id",
            page_id=expected_page_id,
            offset=expected_page_id * PAGE_SIZE + 8,
        )
    supported = (DATA_PAGE_VERSION,) if allow_versions is None else tuple(allow_versions)
    if version not in supported:
        raise _format_error(
            "UNSUPPORTED_DATA_PAGE_VERSION",
            f"不支持的 Catalog page version：{version}，允许 {supported}",
            field="version",
            page_id=expected_page_id,
            offset=expected_page_id * PAGE_SIZE + 24,
        )
    if version == FORMAT_VERSION_V2:
        from .checksum import verify_page

        verify_page(page, page_id=expected_page_id)
    payload_end = CHECKSUM_OFFSET if version == FORMAT_VERSION_V2 else PAGE_SIZE
    if not DATA_PAGE_HEADER_SIZE <= free_start <= free_end <= payload_end:
        raise _format_error(
            "BAD_CATALOG_FREE_RANGE",
            f"Catalog 空闲区间非法：free_start={free_start}, free_end={free_end}",
            field="free_range",
            page_id=expected_page_id,
            offset=expected_page_id * PAGE_SIZE + 16,
        )
    # Catalog 的 table_id 固定为 0；slot_count/flags/reserved 可由后续任务更新。
    if table_id != 0:
        raise _format_error(
            "BAD_CATALOG_TABLE_ID",
            f"Catalog page table_id 必须为 0，实际为 {table_id}",
            field="table_id",
            page_id=expected_page_id,
            offset=expected_page_id * PAGE_SIZE + 20,
        )


class DatabaseFile:
    """可重启打开的 MiniDB 单文件。

    DiskManager 在其上完成页分配和空闲链管理。对象持有一个 ``r+b``
    文件句柄。构造新文件只发生在路径不存在或文件
    长度为 0 时；任何非空文件先完整校验，校验失败不会写入或截断文件。
    """

    _path: Path
    _handle: object
    _superblock: Superblock
    _closed: bool

    def __init__(self, path: Path, handle: object, superblock: Superblock) -> None:
        """仅由 _open 成功路径调用；保存已打开句柄和已验证的页 0 状态。"""

        self._path = path
        self._handle = handle
        self._superblock = superblock
        self._closed = False

    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str],
        *,
        format_version: int | None = None,
        version: int | None = None,
    ) -> Self:
        """供普通启动调用；校验请求版本后转入内部新建或校验流程。"""

        requested = format_version if format_version is not None else version
        if requested not in (None, FORMAT_VERSION, FORMAT_VERSION_V2):
            raise ValueError(f"不支持的数据库格式版本：{requested}")
        return cls._open(path, requested_version=FORMAT_VERSION_V2 if requested == FORMAT_VERSION_V2 else None)

    @classmethod
    def open_v2(cls, path: str | os.PathLike[str]) -> Self:
        """供 v2 实验启动；固定请求版本并转入 _open，不自动升级 v1。"""

        return cls._open(path, requested_version=FORMAT_VERSION_V2)

    @classmethod
    def _open(cls, path: str | os.PathLike[str], *, requested_version: int | None) -> Self:
        """由 open 系列调用；新文件写两页，已有文件只读校验后返回句柄。"""

        database_path = Path(path)
        try:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            exists = database_path.exists()
            file_size = database_path.stat().st_size if exists else 0
            if not exists or file_size == 0:
                handle = database_path.open("w+b")
                try:
                    initial = Superblock.initial(requested_version or FORMAT_VERSION)
                    page0 = initial.encode()
                    page1_header = encode_data_page_header(version=initial.version)
                    page1 = page1_header + bytes(PAGE_SIZE - DATA_PAGE_HEADER_SIZE)
                    if initial.version == FORMAT_VERSION_V2:
                        from .checksum import seal_page

                        page1 = seal_page(page1)
                    handle.write(page0)
                    handle.write(page1)
                    handle.flush()
                    os.fsync(handle.fileno())
                    return cls(database_path, handle, initial)
                except (OSError, ValueError, struct.error, StorageError) as error:
                    _close_quietly(handle)
                    if isinstance(error, OSError):
                        raise _io_error(
                            "INITIALIZE_FAILED",
                            f"初始化数据库文件失败：{error}",
                            path=str(database_path),
                        ) from error
                    raise

            handle = database_path.open("r+b")
            try:
                raw_superblock = _read_exact(handle, PAGE_SIZE, page_id=SUPERBLOCK_PAGE_ID)
                loaded = Superblock.decode(
                    raw_superblock,
                    allow_versions=(FORMAT_VERSION,) if requested_version is None else SUPPORTED_FORMAT_VERSIONS,
                )
                if requested_version is not None and loaded.version != requested_version:
                    raise _format_error(
                        "VERSION_MODE_MISMATCH",
                        f"请求打开 v{requested_version}，文件实际为 v{loaded.version}",
                        field="version",
                        offset=8,
                    )
                actual_size = os.fstat(handle.fileno()).st_size
                loaded.validate_file_size(actual_size)
                raw_catalog = _read_exact(handle, PAGE_SIZE, page_id=loaded.catalog_head)
                _validate_catalog_page(
                    raw_catalog,
                    expected_page_id=loaded.catalog_head,
                    page_count=loaded.page_count,
                    allow_versions=(loaded.version,),
                )
                return cls(database_path, handle, loaded)
            except (OSError, ValueError, struct.error, StorageError) as error:
                _close_quietly(handle)
                if isinstance(error, OSError):
                    raise _io_error(
                        "OPEN_FAILED",
                        f"打开数据库文件失败：{error}",
                        path=str(database_path),
                    ) from error
                raise
        except StorageError:
            raise
        except OSError as error:
            raise _io_error("OPEN_FAILED", f"打开数据库文件失败：{error}", path=str(database_path)) from error

    @classmethod
    def initialize(
        cls,
        path: str | os.PathLike[str],
        *,
        format_version: int | None = None,
        version: int | None = None,
    ) -> Self:
        """供显式建库调用；拒绝非空路径后复用 open 完成两页初始化。"""

        database_path = Path(path)
        if database_path.exists() and database_path.stat().st_size != 0:
            raise _format_error(
                "ALREADY_INITIALIZED",
                "不能覆盖非空数据库文件",
                path=str(database_path),
            )
        return cls.open(database_path, format_version=format_version, version=version)

    @classmethod
    def initialize_v2(cls, path: str | os.PathLike[str]) -> Self:
        """供显式创建 v2 库；拒绝非空文件后复用 open_v2 写入 CRC 格式。"""

        database_path = Path(path)
        if database_path.exists() and database_path.stat().st_size != 0:
            raise _format_error(
                "ALREADY_INITIALIZED",
                "不能覆盖非空数据库文件",
                path=str(database_path),
            )
        return cls.open_v2(database_path)

    @property
    def path(self) -> Path:
        """供 DiskManager 与错误报告读取规范路径；返回初始化时保存的 Path。"""

        return self._path

    @property
    def superblock(self) -> Superblock:
        """供 DiskManager 读取页 0 元数据；返回 frozen 对象，调用方不能原地修改。"""

        return self._superblock

    @property
    def next_table_id(self) -> int:
        """供最终装配注入 CatalogService；转发页 0 的只读表号下界。"""

        return self._superblock.next_table_id

    @property
    def page_count(self) -> int:
        """供页号校验与分配读取总页数；合法范围为 ``0 <= id < page_count``。"""

        return self._superblock.page_count

    @property
    def page_version(self) -> int:
        """供页读写选择格式；v2 的最后四字节由 CRC32 占用。"""

        return self._superblock.version

    @property
    def format_version(self) -> int:
        """提供 page_version 的兼容名称；供 DiskManager 配置读取。"""

        return self.page_version

    def describe_header(self) -> dict[str, dict[str, int | bytes]]:
        """供工作台演示页 0；检查开启后委托 Superblock 生成只读说明。"""

        self._check_open()
        return self._superblock.describe_header()

    @property
    def closed(self) -> bool:
        """供 I/O 入口检查生命周期；返回内部关闭标记。"""

        return self._closed

    def read_page(self, page_id: int) -> bytes:
        """供 DiskManager 读取整页；定位偏移、精确读取并为 v2 验证 CRC。"""

        self._check_open()
        self._check_page_id(page_id)
        try:
            self._handle.seek(page_id * PAGE_SIZE)  # type: ignore[union-attr]
            raw = _read_exact(self._handle, PAGE_SIZE, page_id=page_id)
            if self.page_version == FORMAT_VERSION_V2:
                from .checksum import verify_page

                verify_page(raw, page_id=page_id)
            return raw
        except OSError as error:
            raise _io_error(
                "READ_PAGE_FAILED",
                f"读取 page {page_id} 失败：{error}",
                page_id=page_id,
                offset=page_id * PAGE_SIZE,
            ) from error

    def write_page(self, page_id: int, data: bytes | bytearray | memoryview) -> None:
        """覆盖一个已经存在的完整页。

        BufferPool 和 DiskManager 调用。页写入严格要求一个 ``PAGE_SIZE`` 页，
        且不允许借此改变文件长度。
        新页由 ``append_page`` 分配，避免页数元数据和物理文件长度暂时不一致。
        """

        self._check_open()
        self._check_page_id(page_id)
        raw = bytes(data)
        if len(raw) != PAGE_SIZE:
            raise ValueError(f"页数据必须恰好为 {PAGE_SIZE} 字节，实际为 {len(raw)}")
        if self.page_version == FORMAT_VERSION_V2:
            from .checksum import seal_page

            raw = seal_page(self._normalize_v2_page(raw, page_id=page_id))
        try:
            self._handle.seek(page_id * PAGE_SIZE)  # type: ignore[union-attr]
            written = self._handle.write(raw)  # type: ignore[union-attr]
            if written != PAGE_SIZE:
                raise OSError(f"短写：写入 {written} 字节")
            self._handle.flush()  # type: ignore[union-attr]
            os.fsync(self._handle.fileno())  # type: ignore[union-attr]
        except OSError as error:
            raise _io_error(
                "WRITE_PAGE_FAILED",
                f"写入 page {page_id} 失败：{error}",
                page_id=page_id,
                offset=page_id * PAGE_SIZE,
            ) from error

    def append_page(self, data: bytes | bytearray | memoryview) -> int:
        """供 DiskManager 分配文件尾页；写完整页、同步磁盘，再推进 page_count。"""

        self._check_open()
        raw = bytes(data)
        if len(raw) != PAGE_SIZE:
            raise ValueError(f"页数据必须恰好为 {PAGE_SIZE} 字节，实际为 {len(raw)}")
        old_count = self._superblock.page_count
        old_size = old_count * PAGE_SIZE
        if self.page_version == FORMAT_VERSION_V2:
            from .checksum import seal_page

            raw = seal_page(self._normalize_v2_page(raw, page_id=old_count))
        try:
            actual_size = os.fstat(self._handle.fileno()).st_size  # type: ignore[union-attr]
        except OSError as error:
            raise _io_error("STAT_FAILED", f"读取数据库文件长度失败：{error}") from error
        if actual_size != old_size:
            raise _format_error(
                "BAD_FILE_LENGTH",
                f"追加页之前文件长度错误：期望 {old_size} 字节，实际 {actual_size} 字节",
                field="file_size",
                offset=old_count * PAGE_SIZE,
                expected_length=old_size,
                actual_length=actual_size,
            )
        page_id = old_count
        try:
            self._handle.seek(old_size)  # type: ignore[union-attr]
            written = self._handle.write(raw)  # type: ignore[union-attr]
            if written != PAGE_SIZE:
                raise OSError(f"短写：写入 {written} 字节")
            self._handle.flush()  # type: ignore[union-attr]
            os.fsync(self._handle.fileno())  # type: ignore[union-attr]
            self.persist_superblock(replace(self._superblock, page_count=old_count + 1))
        except StorageError:
            self._rollback_append(old_size)
            raise
        except OSError as error:
            self._rollback_append(old_size)
            raise _io_error(
                "APPEND_PAGE_FAILED",
                f"追加 page {page_id} 失败：{error}",
                page_id=page_id,
                offset=old_size,
            ) from error
        return page_id

    def save_superblock(self, superblock: Superblock) -> None:
        """供 C-B01 和受限更新调用；拒绝改变页数后委托 persist_superblock。"""

        self._check_open()
        if superblock.page_count != self._superblock.page_count:
            # 修改 page_count 必须由后续页分配任务协调文件扩展，避免这里产生半成品。
            raise ValueError("C-B01 的 save_superblock 不允许改变 page_count")
        self.persist_superblock(superblock)

    def persist_superblock(self, superblock: Superblock) -> None:
        """保存页 0，允许调用方已经完成一致的文件扩展。

        DiskManager 页分配与表号更新调用。C-B01 的 ``save_superblock``
        保留严格的同页数约束；页分配器使用
        本方法在追加页后同时更新持久化的 ``page_count``。
        """

        self._check_open()
        try:
            actual_size = os.fstat(self._handle.fileno()).st_size  # type: ignore[union-attr]
        except OSError as error:
            raise _io_error("STAT_FAILED", f"读取数据库文件长度失败：{error}") from error
        superblock.validate_file_size(actual_size)
        if superblock.version != self._superblock.version:
            raise _format_error(
                "VERSION_MODE_MISMATCH",
                f"不能把 v{superblock.version} Superblock 写入 v{self._superblock.version} 文件",
                field="version",
                offset=8,
            )
        encoded = superblock.encode()
        # 复用解码器检查相对页号、版本、保留区与 v2 CRC，保存前不改变文件。
        validated = Superblock.decode(
            encoded,
            allow_versions=(self._superblock.version,),
        )
        if validated != superblock:
            raise ValueError("待保存的 Superblock 未通过固定格式校验")
        try:
            self._handle.seek(0)  # type: ignore[union-attr]
            self._handle.write(encoded)  # type: ignore[union-attr]
            self._handle.flush()  # type: ignore[union-attr]
            os.fsync(self._handle.fileno())  # type: ignore[union-attr]
        except OSError as error:
            raise _io_error("WRITE_SUPERBLOCK_FAILED", f"写入 Superblock 失败：{error}", offset=0) from error
        self._superblock = superblock

    def _rollback_append(self, old_size: int) -> None:
        """由 append_page 失败补偿调用；尽力截回旧长度且不覆盖原始异常。"""

        try:
            self._handle.truncate(old_size)  # type: ignore[union-attr]
            self._handle.flush()  # type: ignore[union-attr]
            os.fsync(self._handle.fileno())  # type: ignore[union-attr]
        except OSError:
            return

    @staticmethod
    def _normalize_v2_page(raw: bytes, *, page_id: int) -> bytes:
        """由 v2 页写入路径调用；全零页补合法页头，并拒绝混入其他版本。"""

        if raw[:DATA_PAGE_HEADER_SIZE] == bytes(DATA_PAGE_HEADER_SIZE):
            return encode_data_page_header(page_id=page_id, version=DATA_PAGE_VERSION_V2) + bytes(
                PAGE_SIZE - DATA_PAGE_HEADER_SIZE
            )
        version = int.from_bytes(raw[24:28], "little")
        if page_id != SUPERBLOCK_PAGE_ID and version != DATA_PAGE_VERSION_V2:
            raise _format_error(
                "MIXED_PAGE_VERSION",
                f"v2 文件的 page {page_id} 必须使用数据页 version=2，实际为 {version}",
                page_id=page_id,
                field="version",
                offset=page_id * PAGE_SIZE + 24,
            )
        return raw

    def update_superblock(self, **changes: int) -> Superblock:
        """供测试和简单元数据更新；replace 构造新对象，经 save_superblock 持久化。"""

        updated = replace(self._superblock, **changes)
        self.save_superblock(updated)
        return updated

    def flush(self) -> None:
        """供 DiskManager 和显式同步调用；刷新 Python 缓冲并执行 fsync。"""

        self._check_open()
        try:
            self._handle.flush()  # type: ignore[union-attr]
            os.fsync(self._handle.fileno())  # type: ignore[union-attr]
        except OSError as error:
            raise _io_error("FLUSH_FAILED", f"刷新数据库文件失败：{error}") from error

    def close(self) -> None:
        """供上下文退出或所有者调用；幂等刷新、fsync、关闭句柄并标记状态。"""

        if self._closed:
            return
        try:
            self._handle.flush()  # type: ignore[union-attr]
            os.fsync(self._handle.fileno())  # type: ignore[union-attr]
            self._handle.close()  # type: ignore[union-attr]
        except OSError as error:
            self._closed = True
            raise _io_error("CLOSE_FAILED", f"关闭数据库文件失败：{error}") from error
        self._closed = True

    def __enter__(self) -> Self:
        """支持 ``with DatabaseFile``；检查未关闭后返回自身。"""

        self._check_open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """由上下文管理器调用；退出时统一关闭数据库文件。"""

        self.close()

    def _check_open(self) -> None:
        """由所有文件操作入口调用；关闭后抛出带路径的 I/O 错误。"""

        if self._closed:
            raise _io_error("CLOSED_DATABASE", "数据库文件已经关闭", path=str(self._path))

    def _check_page_id(self, page_id: int) -> None:
        """由页读写调用；校验整数类型和当前 superblock 页数范围。"""

        if not isinstance(page_id, int) or isinstance(page_id, bool):
            raise ValueError("page_id 必须是整数")
        if not 0 <= page_id < self._superblock.page_count:
            raise _format_error(
                "PAGE_OUT_OF_RANGE",
                f"page_id 超出范围：{page_id}",
                page_id=page_id,
                page_count=self._superblock.page_count,
            )


def _read_exact(handle: object, length: int, *, page_id: int) -> bytes:
    """由打开和读页路径调用；读取固定长度，短读时报告页号与物理偏移。"""

    try:
        data = handle.read(length)  # type: ignore[union-attr]
    except OSError:
        raise
    if len(data) != length:
        raise _format_error(
            "SHORT_READ",
            f"读取 page {page_id} 时得到 {len(data)} 字节，期望 {length} 字节",
            page_id=page_id,
            offset=page_id * PAGE_SIZE,
            expected_length=length,
            actual_length=len(data),
        )
    return data


def open_database(path: str | os.PathLike[str]) -> DatabaseFile:
    """供整合层函数式打开 v1 数据库；直接委托 DatabaseFile.open。"""

    return DatabaseFile.open(path)


def open_database_v2(path: str | os.PathLike[str]) -> DatabaseFile:
    """供整合层函数式打开 v2 文件；委托 open_v2 且不会升级 v1。"""

    return DatabaseFile.open_v2(path)


def initialize_database(path: str | os.PathLike[str]) -> DatabaseFile:
    """供整合层显式初始化 v1 文件；委托 DatabaseFile.initialize。"""

    return DatabaseFile.initialize(path)


def initialize_database_v2(path: str | os.PathLike[str]) -> DatabaseFile:
    """供整合层显式初始化 v2 文件；委托 DatabaseFile.initialize_v2。"""

    return DatabaseFile.initialize_v2(path)


# 便于课程讲义和后续代码使用更短的名称；实现仍只有 DatabaseFile 一份。
MiniDBFile = DatabaseFile


__all__ = [
    "DatabaseFile",
    "MiniDBFile",
    "StorageFormatError",
    "Superblock",
    "encode_data_page_header",
    "initialize_database",
    "initialize_database_v2",
    "open_database",
    "open_database_v2",
]
