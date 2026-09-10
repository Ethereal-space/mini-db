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
from .page import Page, PageFullError, SlotNotFoundError
from .row_codec import RowCodec, RowDecodeError
from .superblock import DatabaseFile, StorageFormatError


def _storage_error(code: str, message: str, **context: object) -> StorageError:
    return StorageError(code, message, span=None, context=context)


@dataclass(frozen=True, slots=True)
class _TableDescriptor:
    table_id: int
    first_page_id: int
    columns: tuple[ColumnMeta, ...]


class TableHeap:
    """实现冻结 ``StoragePort``，并给页式 Catalog 提供内部同构入口。"""

    def __init__(
        self,
        storage: BufferPool | DiskManager | DatabaseFile | str | Path,
        *,
        buffer_pool: BufferPool | None = None,
        buffer_pool_size: int = 8,
        pool_size: int | None = None,
        policy: str = "LRU",
    ) -> None:
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

    @property
    def disk(self) -> DiskManager:
        return self._disk

    @property
    def disk_manager(self) -> DiskManager:
        return self._disk

    @property
    def buffer_pool(self) -> BufferPool:
        return self._buffer

    @property
    def page_count(self) -> int:
        return self._disk.page_count

    @property
    def closed(self) -> bool:
        return self._closed

    def create_table(self, table_id: int, columns: tuple[ColumnMeta, ...]) -> int:
        self._check_open()
        self._validate_schema(table_id, columns)
        first_page_id = self._disk.allocate_page()
        page = Page.new(first_page_id, table_id=table_id)
        with self._buffer.fetch_page(first_page_id) as frame:
            frame.data[:] = page.to_bytes()
            self._buffer.mark_dirty(frame)
        self._disk.update_next_table_id(max(self._disk.next_table_id, table_id + 1))
        self._buffer.flush_all()
        return first_page_id

    def insert(self, table: TableMeta, values: tuple[object, ...]) -> RID:
        descriptor = self._descriptor(table)
        return self._insert_descriptor(descriptor, values)

    def scan(self, table: TableMeta) -> Iterator[StoredRow]:
        descriptor = self._descriptor(table)
        yield from self._scan_descriptor(descriptor)

    def mark_delete(self, table: TableMeta, rid: RID) -> None:
        descriptor = self._descriptor(table)
        self._mark_delete_descriptor(descriptor, rid)

    def flush_all(self) -> None:
        self._check_open()
        self._buffer.flush_all()

    flush = flush_all

    def stats(self) -> BufferStats:
        return self._buffer.stats()

    def table_page_count(self, table: TableMeta | object) -> int:
        descriptor = self._descriptor(table) if isinstance(table, TableMeta) else self._coerce_descriptor(table)
        return len(tuple(self._chain_pages(descriptor)))

    def page_chain(self, table: TableMeta) -> tuple[int, ...]:
        return tuple(self._chain_pages(self._descriptor(table)))

    def catalog_page_count(self) -> int:
        descriptor = _TableDescriptor(0, CATALOG_PAGE_ID, ())
        return len(tuple(self._chain_pages(descriptor, allow_empty_schema=True)))

    def close(self) -> None:
        if self._closed:
            return
        if self._owns_pool:
            self._buffer.close()
        else:
            self._buffer.flush_all()
        self._closed = True

    def __enter__(self) -> "TableHeap":
        self._check_open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def insert_raw(self, table_id: int, first_page_id: int, columns: Sequence[ColumnMeta], values: Sequence[object]) -> RID:
        descriptor = _TableDescriptor(table_id, first_page_id, tuple(columns))
        return self._insert_descriptor(descriptor, tuple(values))

    def scan_raw(self, table_id: int, first_page_id: int, columns: Sequence[ColumnMeta]) -> Iterator[StoredRow]:
        descriptor = _TableDescriptor(table_id, first_page_id, tuple(columns))
        yield from self._scan_descriptor(descriptor, allow_empty_schema=False)

    def mark_delete_raw(self, table_id: int, first_page_id: int, columns: Sequence[ColumnMeta], rid: RID) -> None:
        descriptor = _TableDescriptor(table_id, first_page_id, tuple(columns))
        self._mark_delete_descriptor(descriptor, rid)

    def _insert_descriptor(self, descriptor: _TableDescriptor, values: Sequence[object]) -> RID:
        self._check_open()
        payload = RowCodec.encode(descriptor.columns, values)
        page_id = descriptor.first_page_id
        seen: set[int] = set()
        while True:
            if page_id in seen:
                raise StorageFormatError("TABLE_PAGE_CYCLE", f"表 {descriptor.table_id} 页链形成环", {"page_id": page_id})
            seen.add(page_id)
            next_page_id: int
            with self._buffer.fetch_page(page_id) as frame:
                page = Page.from_bytes(frame.data, expected_page_id=page_id)
                self._check_table_page(page, descriptor)
                try:
                    slot_id = page.insert(payload)
                except PageFullError:
                    next_page_id = page.next_page_id
                else:
                    frame.data[:] = page.to_bytes()
                    self._buffer.mark_dirty(frame)
                    return RID(page_id, slot_id)
            if next_page_id != INVALID_PAGE_ID:
                page_id = next_page_id
                continue
            new_page_id = self._disk.allocate_page()
            new_page = Page.new(new_page_id, table_id=descriptor.table_id)
            slot_id = new_page.insert(payload)
            with self._buffer.fetch_page(new_page_id) as new_frame:
                new_frame.data[:] = new_page.to_bytes()
                self._buffer.mark_dirty(new_frame)
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
            return RID(new_page_id, slot_id)

    def _scan_descriptor(
        self,
        descriptor: _TableDescriptor,
        *,
        allow_empty_schema: bool = False,
    ) -> Iterator[StoredRow]:
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
                    return
            page_id = next_page_id
        raise _storage_error(
            "RID_NOT_IN_TABLE",
            f"RID {rid} 的 page 不属于表 {descriptor.table_id}",
            rid=rid,
            table_id=descriptor.table_id,
        )

    def _chain_pages(self, descriptor: _TableDescriptor, *, allow_empty_schema: bool = False) -> Iterator[int]:
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
        if not isinstance(table, TableMeta):
            raise ValueError("table 必须是冻结 TableMeta")
        return _TableDescriptor(table.table_id, table.first_page_id, table.columns)

    @staticmethod
    def _coerce_descriptor(value: object) -> _TableDescriptor:
        if isinstance(value, _TableDescriptor):
            return value
        raise ValueError("需要 TableMeta 或内部表描述")

    def _check_open(self) -> None:
        if self._closed:
            raise StorageError("CLOSED_STORAGE", "TableHeap 已关闭", span=None, context={})


__all__ = ["TableHeap"]
