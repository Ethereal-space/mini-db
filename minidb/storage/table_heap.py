"""基于 Page、RowCodec、BufferPool 的表页链和真实行存储。"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from minidb.contracts.errors import StorageError
from minidb.contracts.metadata import ColumnMeta, TableMeta
from minidb.contracts.results import BufferStats, RID, StoredRow

from .buffer_pool import BufferPool
from .constants import CATALOG_PAGE_ID, INVALID_PAGE_ID
from .disk_manager import DiskManager
from .free_space_map import FreeSpaceMap
from .page import Page, PageFullError, SLOT_SIZE, SlotNotFoundError
from .row_codec import RowCodec, RowDecodeError
from .superblock import DatabaseFile, StorageFormatError


def _storage_error(code: str, message: str, **context: object) -> StorageError:
    """供表堆边界与 RID 错误分支统一创建 StorageError；附带存储上下文。"""

    return StorageError(code, message, span=None, context=context)


@dataclass(frozen=True, slots=True)
class _TableDescriptor:
    """统一 TableMeta 与 Catalog 原始入口；由 TableHeap 内部方法传递表定位信息。"""

    table_id: int
    first_page_id: int
    columns: tuple[ColumnMeta, ...]


class TableHeap:
    """实现 StoragePort，串联 RowCodec、Page、BufferPool 与 DiskManager 完成行存储。"""

    def __init__(
        self,
        storage: BufferPool | DiskManager | DatabaseFile | str | Path,
        *,
        buffer_pool: BufferPool | None = None,
        buffer_pool_size: int = 8,
        pool_size: int | None = None,
        policy: str = "LRU",
        free_space_map: FreeSpaceMap | None = None,
    ) -> None:
        """由存储装配层创建；按输入类型取得磁盘和缓存所有权，并建立内存 FSM。"""

        if pool_size is not None:
            buffer_pool_size = pool_size
        self._owns_pool = False
        self._owns_disk = False
        if buffer_pool is not None:
            self._buffer = buffer_pool
            self._disk = buffer_pool.disk
        elif isinstance(storage, BufferPool):
            self._buffer = storage
            self._disk = storage.disk
        elif isinstance(storage, DiskManager):
            self._disk = storage
            self._buffer = BufferPool(storage, pool_size=buffer_pool_size, policy=policy, owns_disk=False)
            self._owns_pool = True
        elif isinstance(storage, DatabaseFile):
            self._disk = DiskManager(storage, owns_database=True)
            self._buffer = BufferPool(self._disk, pool_size=buffer_pool_size, policy=policy, owns_disk=True)
            self._owns_pool = True
            self._owns_disk = True
        else:
            self._buffer = BufferPool(storage, pool_size=buffer_pool_size, policy=policy, owns_disk=True)
            self._disk = self._buffer.disk
            self._owns_pool = True
            self._owns_disk = True
        self._closed = False
        # Map 是可选的内存优化；不写入旁路文件，首次遇到某张表时由页链
        # 重新扫描建立。保留同一对象允许调用方观察命中/回退统计。
        self._free_space_map = free_space_map if free_space_map is not None else FreeSpaceMap()

    @property
    def disk(self) -> DiskManager:
        """供 Catalog 仓库和诊断读取底层 DiskManager；不转移所有权。"""

        return self._disk

    @property
    def disk_manager(self) -> DiskManager:
        """提供 disk 的明确别名；供需要页分配接口的内部组件使用。"""

        return self._disk

    @property
    def buffer_pool(self) -> BufferPool:
        """供页式 Catalog 和诊断访问共享缓存；返回当前 BufferPool。"""

        return self._buffer

    @property
    def page_count(self) -> int:
        """供测试和链边界检查读取总页数；转发 DiskManager.page_count。"""

        return self._disk.page_count

    @property
    def closed(self) -> bool:
        """供生命周期检查读取表堆关闭状态。"""

        return self._closed

    @property
    def free_space_map(self) -> FreeSpaceMap:
        """供维护和诊断读取容量提示索引；返回共享 FreeSpaceMap 对象。"""

        return self._free_space_map

    @property
    def fsm(self) -> FreeSpaceMap:
        """提供 free_space_map 的简写别名；便于课程演示与测试。"""

        return self._free_space_map

    def create_table(self, table_id: int, columns: tuple[ColumnMeta, ...]) -> int:
        """供 CREATE 执行器调用；校验 schema、分配首页、写空页并推进表号下界。"""

        self._check_open()
        self._validate_schema(table_id, columns)
        first_page_id = self._disk.allocate_page()
        page = Page.new(first_page_id, table_id=table_id, version=self._page_version)
        with self._buffer.fetch_page(first_page_id) as frame:
            frame.data[:] = page.to_bytes()
            self._buffer.mark_dirty(frame)
            self._free_space_map.refresh(page)
        self._disk.update_next_table_id(max(self._disk.next_table_id, table_id + 1))
        self._buffer.flush_all()
        return first_page_id

    def insert(self, table: TableMeta, values: tuple[object, ...]) -> RID:
        """供 INSERT 执行器调用；把 TableMeta 转为描述后委托真实插入流程。"""

        descriptor = self._descriptor(table)
        return self._insert_descriptor(descriptor, values)

    def scan(self, table: TableMeta) -> Iterator[StoredRow]:
        """供 SeqScan 调用；规范化表描述后按页链惰性产生 StoredRow。"""

        descriptor = self._descriptor(table)
        yield from self._scan_descriptor(descriptor)

    def mark_delete(self, table: TableMeta, rid: RID) -> None:
        """供 DELETE 执行器调用；规范化表描述后在所属页设置 tombstone。"""

        descriptor = self._descriptor(table)
        self._mark_delete_descriptor(descriptor, rid)

    def rebuild_free_space_map(self, table: TableMeta) -> int:
        """供启动或诊断重建 FSM；读取并验证真实页链，再批量生成容量提示。"""

        descriptor = self._descriptor(table)
        pages: list[Page] = []
        for page_id in self._chain_pages(descriptor):
            with self._buffer.fetch_page(page_id) as frame:
                page = Page.from_bytes(frame.data, expected_page_id=page_id)
                self._check_table_page(page, descriptor)
                pages.append(page)
        return self._free_space_map.rebuild(pages, table_id=descriptor.table_id)

    rebuild_fsm = rebuild_free_space_map

    def flush_all(self) -> None:
        """供 StoragePort 提交和关闭前持久化；检查状态后委托 BufferPool。"""

        self._check_open()
        self._buffer.flush_all()

    flush = flush_all

    def stats(self) -> BufferStats:
        """供工作台和执行 Trace 读取缓存统计；委托 BufferPool.stats。"""

        return self._buffer.stats()

    def table_page_count(self, table: TableMeta | object) -> int:
        """供测试和演示统计表页数；转换描述、遍历完整页链后计数。"""

        descriptor = self._descriptor(table) if isinstance(table, TableMeta) else self._coerce_descriptor(table)
        return len(tuple(self._chain_pages(descriptor)))

    def page_chain(self, table: TableMeta) -> tuple[int, ...]:
        """供诊断展示表的物理布局；验证并收集页链为不可变元组。"""

        return tuple(self._chain_pages(self._descriptor(table)))

    def catalog_page_count(self) -> int:
        """供 Catalog 持久化测试统计特殊表页数；以表号 0 遍历 page 1 链。"""

        descriptor = _TableDescriptor(0, CATALOG_PAGE_ID, ())
        return len(tuple(self._chain_pages(descriptor, allow_empty_schema=True)))

    def close(self) -> None:
        """供上下文退出或装配层关闭；按所有权关闭缓存，否则只刷新，并保持幂等。"""

        if self._closed:
            return
        if self._owns_pool:
            self._buffer.close()
        else:
            self._buffer.flush_all()
        self._closed = True

    def __enter__(self) -> "TableHeap":
        """支持 ``with TableHeap``；检查未关闭后返回自身。"""

        self._check_open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """由上下文管理器调用；退出时统一执行 close。"""

        self.close()

    def insert_raw(self, table_id: int, first_page_id: int, columns: Sequence[ColumnMeta], values: Sequence[object]) -> RID:
        """供 PageCatalogRepository 写特殊表；组装内部描述后复用普通插入链。"""

        descriptor = _TableDescriptor(table_id, first_page_id, tuple(columns))
        return self._insert_descriptor(descriptor, tuple(values))

    def scan_raw(self, table_id: int, first_page_id: int, columns: Sequence[ColumnMeta]) -> Iterator[StoredRow]:
        """供 PageCatalogRepository 读特殊表；组装内部描述后复用普通扫描链。"""

        descriptor = _TableDescriptor(table_id, first_page_id, tuple(columns))
        yield from self._scan_descriptor(descriptor, allow_empty_schema=False)

    def mark_delete_raw(self, table_id: int, first_page_id: int, columns: Sequence[ColumnMeta], rid: RID) -> None:
        """供内部特殊表删除记录；组装描述后复用 tombstone 流程。"""

        descriptor = _TableDescriptor(table_id, first_page_id, tuple(columns))
        self._mark_delete_descriptor(descriptor, rid)

    def _insert_descriptor(self, descriptor: _TableDescriptor, values: Sequence[object]) -> RID:
        """由公开和 raw 插入入口调用；编码行、查 FSM/页链，必要时追加页并返回 RID。"""

        self._check_open()
        payload = RowCodec.encode(descriptor.columns, values)
        required = SLOT_SIZE + len(payload)
        # 先建立/刷新当前表的提示。Map 是优化索引，真实页链仍是正确性
        # 来源；链页 ID 也用来过滤来自其他表的旧提示。
        chain = tuple(self._chain_pages(descriptor))
        # 已有提示可能是故意注入的过期估计；只在该表完全没有条目时做
        # 首次重建，命中后再由真实插入结果逐页刷新。
        if not self._free_space_map.snapshot(table_id=descriptor.table_id):
            for candidate_page_id in chain:
                with self._buffer.fetch_page(candidate_page_id) as frame:
                    page = Page.from_bytes(frame.data, expected_page_id=candidate_page_id)
                    self._check_table_page(page, descriptor)
                    self._free_space_map.refresh(page)
        candidates = list(
            self._free_space_map.find(required, table_id=descriptor.table_id)
        )
        ordered_page_ids = [page_id for page_id in candidates if page_id in chain]
        ordered_page_ids.extend(page_id for page_id in chain if page_id not in ordered_page_ids)
        attempted: set[int] = set()
        page_id: int
        for page_id in ordered_page_ids:
            if page_id in attempted:
                continue
            attempted.add(page_id)
            next_page_id: int
            with self._buffer.fetch_page(page_id) as frame:
                page = Page.from_bytes(frame.data, expected_page_id=page_id)
                self._check_table_page(page, descriptor)
                try:
                    slot_id = page.insert(payload)
                except PageFullError:
                    self._free_space_map.refresh(page)
                    continue
                else:
                    frame.data[:] = page.to_bytes()
                    self._buffer.mark_dirty(frame)
                    self._free_space_map.refresh(page)
                    return RID(page_id, slot_id)

        # 候选已失败或没有页可容纳时，按原有页链逻辑在尾部追加。上面
        # 已按 chain 顺序核实过每个页，因而这里不会无限重复旧候选。
        if not chain:
            raise StorageFormatError(
                "EMPTY_TABLE_CHAIN",
                f"表 {descriptor.table_id} 没有可用数据页",
                {"table_id": descriptor.table_id},
            )
        page_id = chain[-1]
        new_page_id = self._disk.allocate_page()
        new_page = Page.new(new_page_id, table_id=descriptor.table_id, version=self._page_version)
        slot_id = new_page.insert(payload)
        with self._buffer.fetch_page(new_page_id) as new_frame:
            new_frame.data[:] = new_page.to_bytes()
            self._buffer.mark_dirty(new_frame)
            self._free_space_map.refresh(new_page)
        with self._buffer.fetch_page(page_id) as old_frame:
            old_page = Page.from_bytes(old_frame.data, expected_page_id=page_id)
            self._check_table_page(old_page, descriptor)
            if old_page.next_page_id != INVALID_PAGE_ID:
                raise StorageError(
                    "PAGE_CHAIN_CHANGED",
                    f"page {page_id} 在追加期间已经有后继页",
                    span=None,
                    context={"page_id": page_id},
                )
            old_page.next_page_id = new_page_id
            old_frame.data[:] = old_page.to_bytes()
            self._buffer.mark_dirty(old_frame)
            self._free_space_map.refresh(old_page)
        return RID(new_page_id, slot_id)

    def _scan_descriptor(
        self,
        descriptor: _TableDescriptor,
        *,
        allow_empty_schema: bool = False,
    ) -> Iterator[StoredRow]:
        """由 scan 入口调用；防环遍历页链、解码存活 payload 并惰性产出行。"""

        self._check_open()
        page_id: int | None = descriptor.first_page_id
        seen: set[int] = set()
        while page_id is not None and page_id != INVALID_PAGE_ID:
            if page_id in seen:
                raise StorageFormatError("TABLE_PAGE_CYCLE", f"表 {descriptor.table_id} 页链形成环", {"page_id": page_id})
            seen.add(page_id)
            with self._buffer.fetch_page(page_id) as frame:
                page = Page.from_bytes(frame.data, expected_page_id=page_id)
                self._check_table_page(page, descriptor, allow_empty_schema=allow_empty_schema)
                rows = tuple(page.iter_live())
                next_page_id = page.next_page_id
            for slot_id, payload in rows:
                try:
                    values = RowCodec.decode(descriptor.columns, payload)
                except RowDecodeError as error:
                    raise StorageFormatError(
                        "ROW_DECODE_FAILED",
                        f"page {page_id} slot {slot_id} 的行无法解码：{error.message}",
                        {"page_id": page_id, "slot_id": slot_id, "rid": RID(page_id, slot_id)},
                    ) from error
                yield StoredRow(RID(page_id, slot_id), values)
            page_id = next_page_id

    def _mark_delete_descriptor(self, descriptor: _TableDescriptor, rid: RID) -> None:
        """由删除入口调用；沿页链定位 RID，设置 tombstone、标脏并刷新 FSM。"""

        self._check_open()
        if not isinstance(rid, RID):
            raise ValueError("rid 必须是冻结 RID")
        page_id: int | None = descriptor.first_page_id
        seen: set[int] = set()
        while page_id is not None and page_id != INVALID_PAGE_ID:
            if page_id in seen:
                raise StorageFormatError("TABLE_PAGE_CYCLE", f"表 {descriptor.table_id} 页链形成环", {"page_id": page_id})
            seen.add(page_id)
            with self._buffer.fetch_page(page_id) as frame:
                page = Page.from_bytes(frame.data, expected_page_id=page_id)
                self._check_table_page(page, descriptor)
                next_page_id = page.next_page_id
                if page_id == rid.page_id:
                    try:
                        changed = page.mark_deleted(rid.slot_id)
                    except SlotNotFoundError as error:
                        raise _storage_error(
                            "RID_NOT_IN_TABLE",
                            f"RID {rid} 的 slot 不属于表 {descriptor.table_id}",
                            rid=rid,
                            page_id=page_id,
                            slot_id=rid.slot_id,
                        ) from error
                    if changed:
                        frame.data[:] = page.to_bytes()
                        self._buffer.mark_dirty(frame)
                        self._free_space_map.refresh(page)
                    return
            page_id = next_page_id
        raise _storage_error(
            "RID_NOT_IN_TABLE",
            f"RID {rid} 的 page 不属于表 {descriptor.table_id}",
            rid=rid,
            table_id=descriptor.table_id,
        )

    def _chain_pages(self, descriptor: _TableDescriptor, *, allow_empty_schema: bool = False) -> Iterator[int]:
        """由插入、扫描和诊断共用；逐页验证归属与后继边界，并检测链环。"""

        page_id: int | None = descriptor.first_page_id
        seen: set[int] = set()
        while page_id is not None and page_id != INVALID_PAGE_ID:
            if page_id in seen:
                raise StorageFormatError("TABLE_PAGE_CYCLE", f"表 {descriptor.table_id} 页链形成环", {"page_id": page_id})
            seen.add(page_id)
            with self._buffer.fetch_page(page_id) as frame:
                page = Page.from_bytes(frame.data, expected_page_id=page_id)
                self._check_table_page(page, descriptor, allow_empty_schema=allow_empty_schema)
                next_page_id = page.next_page_id
            yield page_id
            page_id = next_page_id

    def _check_table_page(self, page: Page, descriptor: _TableDescriptor, *, allow_empty_schema: bool = False) -> None:
        """由所有页链操作调用；核对 table_id、schema 要求和后继页范围。"""

        if page.table_id != descriptor.table_id:
            raise StorageFormatError(
                "TABLE_ID_MISMATCH",
                f"page {page.page_id} 的 table_id={page.table_id}，期望 {descriptor.table_id}",
                {"page_id": page.page_id, "expected_table_id": descriptor.table_id},
            )
        if not allow_empty_schema and not descriptor.columns:
            raise ValueError("普通表必须提供非空 schema")
        if page.next_page_id != INVALID_PAGE_ID and page.next_page_id >= self._disk.page_count:
            raise StorageFormatError(
                "NEXT_PAGE_OUT_OF_RANGE",
                f"page {page.page_id} 的后继 {page.next_page_id} 超出 page_count",
                {"page_id": page.page_id, "next_page_id": page.next_page_id},
            )

    @staticmethod
    def _validate_schema(table_id: int, columns: Sequence[ColumnMeta]) -> None:
        """由 create_table 调用；校验表号、非空列集及连续 ordinal。"""

        if not isinstance(table_id, int) or isinstance(table_id, bool) or table_id < 1:
            raise ValueError("table_id 必须是大于等于 1 的整数")
        schema = tuple(columns)
        if not schema:
            raise ValueError("columns 不能为空")
        for index, column in enumerate(schema):
            if not isinstance(column, ColumnMeta) or column.ordinal != index:
                raise ValueError("columns 必须由 ordinal 连续的 ColumnMeta 组成")

    @staticmethod
    def _descriptor(table: TableMeta) -> _TableDescriptor:
        """由公开 StoragePort 方法调用；验证冻结 TableMeta 并提取内部描述。"""

        if not isinstance(table, TableMeta):
            raise ValueError("table 必须是冻结 TableMeta")
        return _TableDescriptor(table.table_id, table.first_page_id, table.columns)

    @staticmethod
    def _coerce_descriptor(value: object) -> _TableDescriptor:
        """由兼容诊断入口调用；只接受已构造的内部描述，否则拒绝。"""

        if isinstance(value, _TableDescriptor):
            return value
        raise ValueError("需要 TableMeta 或内部表描述")

    def _check_open(self) -> None:
        """由所有公开存储操作调用；关闭后抛出明确生命周期错误。"""

        if self._closed:
            raise StorageError("CLOSED_STORAGE", "TableHeap 已关闭", span=None, context={})

    @property
    def _page_version(self) -> int:
        """供创建新 Page 选择格式；从 DiskManager 读取版本，缺省兼容 v1。"""

        return int(getattr(self._disk, "page_version", 1))


__all__ = ["TableHeap"]
