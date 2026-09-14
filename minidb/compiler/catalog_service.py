"""成员 B 的内存 Catalog 服务实现，对应任务 B-B01。"""

from __future__ import annotations

from collections.abc import Iterable

from minidb.contracts.errors import SemanticError, StorageError
from minidb.contracts.metadata import TableMeta, normalize_identifier
from minidb.contracts.ports import CatalogPort, CatalogRepositoryPort


MAX_TABLE_ID = 2_147_483_647


class CatalogService(CatalogPort):
    """Cache table metadata while delegating durable storage to a repository."""

    def __init__(
        self,
        repository: CatalogRepositoryPort,
        initial_next_table_id: int = 1,
    ) -> None:
        if (
            isinstance(initial_next_table_id, bool)
            or not isinstance(initial_next_table_id, int)
            or initial_next_table_id < 1
        ):
            raise ValueError("initial_next_table_id 必须是正整数")

        self._repository = repository
        self._tables_by_name: dict[str, TableMeta] = {}
        self._table_ids: set[int] = set()
        loaded_tables = repository.load_tables()
        self._load_tables(loaded_tables)
        highest_loaded_id = max(self._table_ids, default=0) + 1
        self._next_table_id = max(highest_loaded_id, initial_next_table_id)

    def _load_tables(self, tables: Iterable[TableMeta]) -> None:
        for table in tables:
            try:
                self._validate_table_shape(table)
            except (TypeError, ValueError) as error:
                raise StorageError(
                    "CATALOG_CORRUPT",
                    f"Catalog 元数据无效: {error}",
                ) from error

            normalized_name = table.name
            if normalized_name in self._tables_by_name:
                raise StorageError(
                    "CATALOG_CORRUPT",
                    f"Catalog 包含重复表名 {normalized_name!r}",
                )
            if table.table_id in self._table_ids:
                raise StorageError(
                    "CATALOG_CORRUPT",
                    f"Catalog 包含重复表号 {table.table_id}",
                )
            self._tables_by_name[normalized_name] = table
            self._table_ids.add(table.table_id)

    @staticmethod
    def _validate_table_shape(table: TableMeta) -> None:
        if not isinstance(table, TableMeta):
            raise TypeError("表元数据必须是 TableMeta")
        if not 1 <= table.table_id <= MAX_TABLE_ID:
            raise ValueError("表号必须位于 1 到 2147483647")
        if table.name != normalize_identifier(table.name):
            raise ValueError("表名必须是小写规范标识符")
        if not table.columns:
            raise ValueError("表至少需要一列")
        ordinals = tuple(column.ordinal for column in table.columns)
        if ordinals != tuple(range(len(table.columns))):
            raise ValueError("列序号必须从 0 连续递增")
        names = tuple(column.name for column in table.columns)
        if len(names) != len(set(names)):
            raise ValueError("列名不能重复")

    @staticmethod
    def _normalize_name(name: str) -> str:
        try:
            return normalize_identifier(name)
        except (TypeError, ValueError) as error:
            raise SemanticError(
                "INVALID_IDENTIFIER",
                f"非法表名 {name!r}",
            ) from error

    def table_exists(self, name: str) -> bool:
        return self._normalize_name(name) in self._tables_by_name

    def get_table(self, name: str) -> TableMeta:
        normalized_name = self._normalize_name(name)
        try:
            return self._tables_by_name[normalized_name]
        except KeyError as error:
            raise SemanticError(
                "TABLE_NOT_FOUND",
                f"表 {normalized_name!r} 不存在",
                context={"table": normalized_name},
            ) from error

    def list_tables(self) -> list[TableMeta]:
        return sorted(self._tables_by_name.values(), key=lambda table: table.table_id)

    def allocate_table_id(self) -> int:
        if self._next_table_id > MAX_TABLE_ID:
            raise StorageError(
                "ID_EXHAUSTED",
                "表号已耗尽",
            )
        allocated_id = self._next_table_id
        self._next_table_id += 1
        return allocated_id

    def register_table(self, table: TableMeta) -> None:
        try:
            self._validate_table_shape(table)
        except (TypeError, ValueError) as error:
            raise SemanticError("INVALID_TABLE_METADATA", str(error)) from error

        if table.name in self._tables_by_name:
            raise SemanticError(
                "DUPLICATE_TABLE_NAME",
                f"表名 {table.name!r} 已存在",
                context={"table": table.name},
            )
        if table.table_id in self._table_ids:
            raise SemanticError(
                "DUPLICATE_TABLE_ID",
                f"表号 {table.table_id} 已存在",
                context={"table_id": table.table_id},
            )

        self._repository.save_table(table)
        self._tables_by_name[table.name] = table
        self._table_ids.add(table.table_id)
        self._next_table_id = max(self._next_table_id, table.table_id + 1)


__all__ = ["CatalogService", "MAX_TABLE_ID"]
