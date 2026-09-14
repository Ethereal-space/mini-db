from __future__ import annotations

import pytest

from minidb.compiler import planner
from minidb.contracts.ast import ColumnDef, CreateTableStmt, Identifier, SelectStmt
from minidb.contracts.bound import (
    BoundBinary,
    BoundColumn,
    BoundCreate,
    BoundDelete,
    BoundInsert,
    BoundLiteral,
    BoundSelect,
)
from minidb.contracts.errors import SemanticError
from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta
from minidb.contracts.plans import (
    CreateTablePlan,
    DeletePlan,
    FilterPlan,
    InsertPlan,
    ProjectPlan,
    SeqScanPlan,
)
from minidb.contracts.results import TraceEvent
from minidb.contracts.source import Position, Span


def span(offset: int = 0) -> Span:
    position = Position(offset=offset, line=1, column=offset + 1)
    return Span.at(position)


def student_table() -> TableMeta:
    return TableMeta(
        table_id=7,
        name="student",
        first_page_id=3,
        columns=(
            ColumnMeta("id", DataType.INT, 0),
            ColumnMeta("name", DataType.VARCHAR, 1),
            ColumnMeta("age", DataType.INT, 2),
        ),
    )


def age_predicate() -> BoundBinary:
    return BoundBinary(
        ">=",
        BoundColumn(2, "age", DataType.INT, span(4)),
        BoundLiteral(18, DataType.INT, span(5)),
        DataType.BOOL,
        span(4),
    )


def create_stmt(table: TableMeta) -> CreateTableStmt:
    return CreateTableStmt(
        Identifier(table.name, span()),
        tuple(
            ColumnDef(Identifier(column.name, span(column.ordinal + 1)), column.dtype.value, span(column.ordinal + 1))
            for column in table.columns
        ),
        span(),
    )


class FakeCatalog:
    def __init__(self, table: TableMeta | None = None) -> None:
        self.table = table
        self.table_exists_calls = 0
        self.get_table_calls = 0
        self.allocate_calls = 0
        self.register_calls = 0

    def table_exists(self, name: str) -> bool:
        self.table_exists_calls += 1
        return self.table is not None and self.table.name == name

    def get_table(self, name: str) -> TableMeta:
        self.get_table_calls += 1
        if self.table is None or self.table.name != name:
            raise KeyError(name)
        return self.table

    def allocate_table_id(self) -> int:
        self.allocate_calls += 1
        raise AssertionError("compile 不应分配表号")

    def register_table(self, table: TableMeta) -> None:
        self.register_calls += 1
        raise AssertionError("compile 不应注册表")


def test_select_plan_shape_with_and_without_filter() -> None:
    table = student_table()
    filtered = BoundSelect(table, (1,), ("name",), age_predicate(), span())
    unfiltered = BoundSelect(table, (0, 1, 2), ("id", "name", "age"), None, span(10))

    filtered_plan = planner.build_plan(filtered)
    unfiltered_plan = planner.build_plan(unfiltered)

    assert isinstance(filtered_plan, ProjectPlan)
    assert filtered_plan.indices == (1,)
    assert filtered_plan.names == ("name",)
    assert isinstance(filtered_plan.child, FilterPlan)
    assert isinstance(filtered_plan.child.child, SeqScanPlan)
    assert filtered_plan.child.child.table == table
    assert filtered_plan.span == filtered.span

    assert isinstance(unfiltered_plan, ProjectPlan)
    assert unfiltered_plan.indices == (0, 1, 2)
    assert isinstance(unfiltered_plan.child, SeqScanPlan)
    assert unfiltered_plan.child.table == table
    assert unfiltered_plan.span == unfiltered.span


def test_insert_plan_contains_schema_order_values() -> None:
    table = student_table()
    bound = BoundInsert(table, (1, "Alice", 20), span())

    plan = planner.build_plan(bound)

    assert isinstance(plan, InsertPlan)
    assert plan.values == (1, "Alice", 20)
    assert plan.table.table_id == table.table_id
    assert plan.table == table
    assert plan.span == bound.span


def test_create_plan_does_not_allocate_identity() -> None:
    table = student_table()
    bound = BoundCreate(table.name, table.columns, span())
    catalog = FakeCatalog()
    compiler = planner.Compiler()

    plan = compiler.compile(
        create_stmt(table),
        catalog,
    )

    assert isinstance(plan, CreateTablePlan)
    assert plan.name == table.name
    assert plan.columns == table.columns
    assert catalog.allocate_calls == catalog.register_calls == 0


def test_delete_plan_retains_rid_path() -> None:
    table = student_table()
    all_rows = BoundDelete(table, None, span())
    matching_rows = BoundDelete(table, age_predicate(), span(10))

    all_plan = planner.build_plan(all_rows)
    matching_plan = planner.build_plan(matching_rows)

    assert isinstance(all_plan, DeletePlan)
    assert isinstance(all_plan.child, SeqScanPlan)
    assert all_plan.table == all_plan.child.table == table

    assert isinstance(matching_plan, DeletePlan)
    assert isinstance(matching_plan.child, FilterPlan)
    assert isinstance(matching_plan.child.child, SeqScanPlan)
    assert matching_plan.table == matching_plan.child.child.table == table
    assert not isinstance(matching_plan.child, ProjectPlan)


def test_compile_stops_after_semantic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    table = student_table()
    catalog = FakeCatalog(table)
    events: list[TraceEvent] = []
    compiler = planner.Compiler(on_trace=events.append)
    statement = SelectStmt(
        table=Identifier("student", span(1)),
        columns=(Identifier("missing", span(2)),),
        where=None,
        span=span(1),
    )
    build_calls = 0

    def unexpected_build(bound: object) -> object:
        nonlocal build_calls
        build_calls += 1
        raise AssertionError("语义失败后不应构造计划")

    monkeypatch.setattr(planner, "build_plan", unexpected_build)

    with pytest.raises(SemanticError, match="missing") as raised:
        compiler.compile(statement, catalog)

    assert raised.value.stage == "SEMANTIC"
    assert raised.value.span == statement.columns[0].span
    assert build_calls == 0
    assert events == []
    assert catalog.allocate_calls == catalog.register_calls == 0
