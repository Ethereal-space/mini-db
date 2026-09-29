from __future__ import annotations

import pytest

from minidb.compiler.semantic import bind_delete, bind_projection, bind_select, resolve_column
from minidb.contracts.ast import BinaryExpr, DeleteStmt, Identifier, SelectStmt
from minidb.contracts.bound import BoundBinary, BoundColumn, BoundDelete, BoundSelect
from minidb.contracts.errors import SemanticError
from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta
from minidb.contracts.source import Position, Span


def span(start: int, end: int | None = None) -> Span:
    finish = start if end is None else end
    return Span(
        Position(start, 1, start + 1),
        Position(finish, 1, finish + 1),
    )


def student_table() -> TableMeta:
    return TableMeta(
        table_id=11,
        name="student",
        first_page_id=23,
        columns=(
            ColumnMeta("id", DataType.INT, 0),
            ColumnMeta("name", DataType.VARCHAR, 1),
            ColumnMeta("age", DataType.INT, 2),
        ),
    )


class ReadOnlyCatalog:
    def __init__(self, table: TableMeta | None) -> None:
        self.table = table
        self.write_called = False

    def table_exists(self, name: str) -> bool:
        return self.table is not None and name == self.table.name

    def get_table(self, name: str) -> TableMeta:
        if self.table is None or name != self.table.name:
            raise KeyError(name)
        return self.table

    def allocate_table_id(self) -> int:
        self.write_called = True
        raise AssertionError("binding must not allocate")

    def register_table(self, table: TableMeta) -> None:
        self.write_called = True
        raise AssertionError("binding must not register")

    def list_tables(self) -> list[TableMeta]:
        self.write_called = True
        raise AssertionError("binding must not list through this path")


def test_resolve_column_returns_ordinal_type_and_span() -> None:
    table = student_table()
    source_span = span(7, 10)
    bound = resolve_column("AGE", table, source_span)
    assert bound == BoundColumn(2, "age", DataType.INT, source_span)


def test_select_star_expands_schema_order() -> None:
    table = student_table()
    stmt = SelectStmt(Identifier("student", span(0)), None, None, span(0, 20))
    bound = bind_select(stmt, ReadOnlyCatalog(table))
    assert bound.indices == (0, 1, 2)
    assert bound.names == ("id", "name", "age")


def test_projection_preserves_order_and_duplicates() -> None:
    table = student_table()
    columns = (
        Identifier("name", span(1, 5)),
        Identifier("id", span(6, 8)),
        Identifier("name", span(9, 13)),
    )
    stmt = SelectStmt(Identifier("student", span(0)), columns, None, span(0, 20))
    bound = bind_select(stmt, ReadOnlyCatalog(table))
    assert bound.indices == (1, 0, 1)
    assert bound.names == ("name", "id", "name")
    assert stmt.columns == columns


def test_unknown_table_and_column_keep_locations() -> None:
    catalog = ReadOnlyCatalog(None)
    unknown_table = SelectStmt(Identifier("ghost", span(2, 7)), None, None, span(0, 10))
    with pytest.raises(SemanticError) as table_error:
        bind_select(unknown_table, catalog)
    assert table_error.value.stage == "SEMANTIC"
    assert table_error.value.span == span(2, 7)
    assert "ghost" in table_error.value.message

    table = student_table()
    unknown_projection = SelectStmt(
        Identifier("student", span(0)),
        (Identifier("score", span(11, 16)),),
        None,
        span(0, 20),
    )
    with pytest.raises(SemanticError) as projection_error:
        bind_select(unknown_projection, ReadOnlyCatalog(table))
    assert projection_error.value.span == span(11, 16)
    assert "score" in projection_error.value.message

    unknown_where = SelectStmt(
        Identifier("student", span(0)),
        None,
        BinaryExpr(">", Identifier("score", span(21, 26)), Identifier("age", span(27, 30)), span(21, 30)),
        span(0, 30),
    )
    with pytest.raises(SemanticError) as where_error:
        bind_select(unknown_where, ReadOnlyCatalog(table))
    assert where_error.value.span == span(21, 26)
    assert "score" in where_error.value.message


def test_delete_binds_predicate_to_original_table() -> None:
    table = student_table()
    predicate = BinaryExpr(">=", Identifier("age", span(20, 23)), Identifier("id", span(24, 26)), span(20, 26))
    stmt = DeleteStmt(Identifier("student", span(0, 7)), predicate, span(0, 26))
    catalog = ReadOnlyCatalog(table)
    bound = bind_delete(stmt, catalog)
    assert isinstance(bound, BoundDelete)
    assert bound.table.table_id == 11
    assert bound.table.first_page_id == 23
    assert isinstance(bound.where, BoundBinary)
    assert isinstance(bound.where.left, BoundColumn)
    assert bound.where.left.index == 2
    assert bound.where.left.name == "age"
    assert not catalog.write_called
