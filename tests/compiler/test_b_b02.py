from __future__ import annotations

import pytest

from minidb.compiler.semantic import analyze_create, analyze_insert
from minidb.contracts.ast import ColumnDef, CreateTableStmt, Identifier, InsertStmt, Literal
from minidb.contracts.errors import SemanticError
from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta
from minidb.contracts.source import Position, Span


def span(offset: int = 0) -> Span:
    position = Position(offset=offset, line=1, column=offset + 1)
    return Span.at(position)


def identifier(name: str, offset: int = 0) -> Identifier:
    return Identifier(name=name, span=span(offset))


def create_stmt(
    name: str = "student",
    columns: tuple[tuple[str, str], ...] = (
        ("id", "INT"),
        ("name", "VARCHAR"),
        ("age", "INT"),
    ),
) -> CreateTableStmt:
    return CreateTableStmt(
        name=identifier(name),
        columns=tuple(
            ColumnDef(identifier(column_name, index + 1), dtype, span(index + 1))
            for index, (column_name, dtype) in enumerate(columns)
        ),
        span=span(),
    )


def student_table() -> TableMeta:
    return TableMeta(
        table_id=1,
        name="student",
        first_page_id=3,
        columns=(
            ColumnMeta("id", DataType.INT, 0),
            ColumnMeta("name", DataType.VARCHAR, 1),
            ColumnMeta("age", DataType.INT, 2),
        ),
    )


class FakeCatalog:
    def __init__(self, tables: tuple[TableMeta, ...] = ()) -> None:
        self.tables = {table.name: table for table in tables}
        self.exists_calls = 0
        self.get_calls = 0
        self.allocate_calls = 0
        self.register_calls = 0

    def table_exists(self, name: str) -> bool:
        self.exists_calls += 1
        return name in self.tables

    def get_table(self, name: str) -> TableMeta:
        self.get_calls += 1
        return self.tables[name]

    def allocate_table_id(self) -> int:
        self.allocate_calls += 1
        raise AssertionError("B-B02 不应分配表号")

    def register_table(self, table: TableMeta) -> None:
        self.register_calls += 1
        raise AssertionError("B-B02 不应注册表")


def insert_stmt(
    columns: tuple[str, ...] | None,
    values: tuple[object, ...],
) -> InsertStmt:
    return InsertStmt(
        table=identifier("student"),
        columns=None if columns is None else tuple(identifier(name) for name in columns),
        values=tuple(Literal(value, span(index)) for index, value in enumerate(values)),
        span=span(),
    )


def test_create_validates_without_mutation() -> None:
    catalog = FakeCatalog()
    bound = analyze_create(create_stmt(), catalog)

    assert bound.name == "student"
    assert [(column.name, column.dtype, column.ordinal) for column in bound.columns] == [
        ("id", DataType.INT, 0),
        ("name", DataType.VARCHAR, 1),
        ("age", DataType.INT, 2),
    ]
    assert catalog.exists_calls == 1
    assert catalog.allocate_calls == catalog.register_calls == 0

    with pytest.raises(SemanticError, match="student"):
        analyze_create(create_stmt(), FakeCatalog((student_table(),)))
    with pytest.raises(SemanticError, match="重复列名"):
        analyze_create(create_stmt(columns=(("id", "INT"), ("id", "INT"))), catalog)


def test_insert_reorders_explicit_columns() -> None:
    expected_table = student_table()
    catalog = FakeCatalog((expected_table,))
    statement = insert_stmt(("age", "id", "name"), (20, 1, "张三"))

    bound = analyze_insert(statement, catalog)

    assert bound.table is expected_table
    assert bound.values == (1, "张三", 20)
    assert statement.columns == tuple(identifier(name) for name in ("age", "id", "name"))
    assert catalog.allocate_calls == catalog.register_calls == 0


def test_insert_without_columns_uses_schema_order() -> None:
    catalog = FakeCatalog((student_table(),))

    bound = analyze_insert(insert_stmt(None, (1, "Alice", 20)), catalog)
    assert bound.values == (1, "Alice", 20)

    with pytest.raises(SemanticError, match="期望 3 实得 2"):
        analyze_insert(insert_stmt(None, (1, "Alice")), catalog)
    assert catalog.allocate_calls == catalog.register_calls == 0


def test_insert_rejects_duplicate_unknown_and_missing_columns() -> None:
    catalog = FakeCatalog((student_table(),))

    with pytest.raises(SemanticError, match="列名重复"):
        analyze_insert(insert_stmt(("id", "id", "age"), (1, 2, 20)), catalog)
    with pytest.raises(SemanticError, match="score"):
        analyze_insert(insert_stmt(("id", "score", "age"), (1, 2, 20)), catalog)
    with pytest.raises(SemanticError, match="完整列集合"):
        analyze_insert(insert_stmt(("id", "name"), (1, "Alice")), catalog)
    assert catalog.allocate_calls == catalog.register_calls == 0


def test_literal_boundaries_and_utf8_length() -> None:
    catalog = FakeCatalog((student_table(),))
    assert analyze_insert(insert_stmt(None, (-2_147_483_648, "a", 2_147_483_647)), catalog)
    assert analyze_insert(insert_stmt(None, (1, "张" * 85, 20)), catalog)

    with pytest.raises(SemanticError, match="INT32"):
        analyze_insert(insert_stmt(None, (2_147_483_648, "Alice", 20)), catalog)
    with pytest.raises(SemanticError, match="255 字节"):
        analyze_insert(insert_stmt(None, (1, "张" * 86, 20)), catalog)
    with pytest.raises(SemanticError, match="INT"):
        analyze_insert(insert_stmt(None, ("1", "Alice", 20)), catalog)
