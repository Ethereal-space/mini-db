"""把 Catalog 作为 table_id=0 的页式特殊表持久化。"""

from __future__ import annotations

from collections import defaultdict

from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta, normalize_identifier
from minidb.contracts.ports import CatalogRepositoryPort
from minidb.contracts.results import RID

from .constants import CATALOG_PAGE_ID
from .table_heap import TableHeap
from .superblock import StorageFormatError


CATALOG_TABLE_ID = 0
CATALOG_FIRST_PAGE_ID = CATALOG_PAGE_ID
CATALOG_PAGE = CATALOG_FIRST_PAGE_ID
CATALOG_COLUMNS: tuple[ColumnMeta, ...] = (
    ColumnMeta("table_id", DataType.INT, 0),
    ColumnMeta("table_name", DataType.VARCHAR, 1),
    ColumnMeta("first_page_id", DataType.INT, 2),
    ColumnMeta("column_ordinal", DataType.INT, 3),
    ColumnMeta("column_name", DataType.VARCHAR, 4),
    ColumnMeta("column_type", DataType.VARCHAR, 5),
)
CATALOG_SCHEMA = CATALOG_COLUMNS


class PageCatalogRepository(CatalogRepositoryPort):
    """由 CatalogService 调用；通过 TableHeap 的 table_id=0 特殊表保存/恢复元数据，不走旁路文件。"""

    def __init__(self, storage: TableHeap) -> None:
        """由整合层装配；校验并保存 TableHeap，后续所有 Catalog I/O 都复用它。"""

        if not isinstance(storage, TableHeap):
            raise ValueError("PageCatalogRepository 需要 TableHeap")
        self._storage = storage

    @property
    def storage(self) -> TableHeap:
        """供装配与诊断读取底层 TableHeap；直接返回共享实例，不产生 I/O。"""

        return self._storage

    @property
    def first_page_id(self) -> int:
        """供上层定位 Catalog 页链；返回 Superblock 约定的固定首页号。"""

        return CATALOG_FIRST_PAGE_ID

    @property
    def schema(self) -> tuple[ColumnMeta, ...]:
        """供 Catalog 行编解码调用；返回冻结的六列系统表 schema。"""

        return CATALOG_COLUMNS

    catalog_schema = schema

    def save_table(self, table: TableMeta) -> None:
        """由 CatalogService 注册表时调用；删除同 ID 旧行、逐列写新行，最后统一刷盘。"""

        if not isinstance(table, TableMeta):
            raise ValueError("table 必须是冻结 TableMeta")
        existing = list(self._storage.scan_raw(CATALOG_TABLE_ID, CATALOG_FIRST_PAGE_ID, CATALOG_COLUMNS))
        for row in existing:
            if row.values[0] == table.table_id:
                self._storage.mark_delete_raw(CATALOG_TABLE_ID, CATALOG_FIRST_PAGE_ID, CATALOG_COLUMNS, row.rid)
        for column in table.columns:
            self._storage.insert_raw(
                CATALOG_TABLE_ID,
                CATALOG_FIRST_PAGE_ID,
                CATALOG_COLUMNS,
                (
                    table.table_id,
                    table.name,
                    table.first_page_id,
                    column.ordinal,
                    column.name,
                    column.dtype.value,
                ),
            )
        self._storage.flush_all()

    def load_tables(self) -> list[TableMeta]:
        """由 CatalogService 启动恢复调用；扫描系统表、按 table_id 分组校验，再重建 TableMeta 列表。"""

        grouped: defaultdict[int, list[tuple[RID, tuple[int | str, ...]]]] = defaultdict(list)
        for row in self._storage.scan_raw(CATALOG_TABLE_ID, CATALOG_FIRST_PAGE_ID, CATALOG_COLUMNS):
            values = row.values
            if len(values) != len(CATALOG_COLUMNS):
                raise StorageFormatError(
                    "CATALOG_ROW_WIDTH",
                    f"Catalog 行宽度错误：{len(values)}",
                    {"page_id": row.rid.page_id, "slot_id": row.rid.slot_id},
                )
            table_id, table_name, first_page_id, ordinal, column_name, column_type = values
            if not isinstance(table_id, int) or table_id < 1:
                raise self._format_row(row.rid, "table_id 必须为正整数")
            if not isinstance(table_name, str) or not isinstance(first_page_id, int):
                raise self._format_row(row.rid, "table_name/first_page_id 类型非法")
            if first_page_id < 1:
                raise self._format_row(row.rid, "first_page_id 必须大于等于 1")
            if not isinstance(ordinal, int) or ordinal < 0:
                raise self._format_row(row.rid, "column_ordinal 必须为非负整数")
            if not isinstance(column_name, str) or not isinstance(column_type, str):
                raise self._format_row(row.rid, "column_name/column_type 类型非法")
            grouped[table_id].append((row.rid, values))

        tables: list[TableMeta] = []
        names: dict[str, int] = {}
        for table_id in sorted(grouped):
            rows = grouped[table_id]
            table_name = rows[0][1][1]
            first_page_id = rows[0][1][2]
            if any(values[1] != table_name or values[2] != first_page_id for _rid, values in rows):
                raise self._format_row(rows[0][0], "同一表的名称或首数据页不一致")
            try:
                normalized_name = normalize_identifier(table_name)
            except ValueError as error:
                raise self._format_row(rows[0][0], f"Catalog 表名非法：{table_name!r}") from error
            if normalized_name != table_name:
                raise self._format_row(rows[0][0], "Catalog 表名必须是小写规范名")
            old_id = names.get(normalized_name)
            if old_id is not None and old_id != table_id:
                raise self._format_row(rows[0][0], "Catalog 中存在重复表名")
            names[normalized_name] = table_id
            by_ordinal: dict[int, ColumnMeta] = {}
            for rid, values in rows:
                ordinal = values[3]
                if ordinal in by_ordinal:
                    raise self._format_row(rid, f"column_ordinal {ordinal} 重复")
                column_name = values[4]
                try:
                    normalized_column = normalize_identifier(column_name)
                except ValueError as error:
                    raise self._format_row(rid, f"Catalog 列名非法：{column_name!r}") from error
                if normalized_column != column_name:
                    raise self._format_row(rid, "Catalog 列名必须是小写规范名")
                try:
                    dtype = DataType(values[5])
                except ValueError as error:
                    raise self._format_row(rid, f"Catalog 列类型不受支持：{values[5]!r}") from error
                if dtype is DataType.BOOL:
                    raise self._format_row(rid, "Catalog 不能持久化 BOOL 列")
                try:
                    by_ordinal[ordinal] = ColumnMeta(normalized_column, dtype, ordinal)
                except ValueError as error:
                    raise self._format_row(rid, f"Catalog 列定义非法：{error}") from error
            if tuple(sorted(by_ordinal)) != tuple(range(len(by_ordinal))):
                raise self._format_row(rows[0][0], "Catalog column_ordinal 必须从 0 连续递增")
            try:
                tables.append(TableMeta(table_id, table_name, tuple(by_ordinal[i] for i in range(len(by_ordinal))), first_page_id))
            except ValueError as error:
                raise self._format_row(rows[0][0], f"Catalog 表定义非法：{error}") from error
        return tables

    @staticmethod
    def _format_row(rid: RID, message: str) -> StorageFormatError:
        """供恢复校验失败分支调用；把 RID 和原因包装成统一 Catalog 损坏错误。"""

        return StorageFormatError(
            "CORRUPT_CATALOG",
            message,
            {"page_id": rid.page_id, "slot_id": rid.slot_id},
        )


CatalogRepository = PageCatalogRepository


__all__ = [
    "CATALOG_COLUMNS",
    "CATALOG_FIRST_PAGE_ID",
    "CATALOG_PAGE",
    "CATALOG_SCHEMA",
    "CATALOG_TABLE_ID",
    "CatalogRepository",
    "PageCatalogRepository",
]
