from __future__ import annotations

import pytest

from minidb.compiler.update_planner import build_update_plan, bind_assignments
from minidb.contracts.ast import BinaryExpr, Identifier, Literal
from minidb.contracts.bound import BoundColumn
from minidb.contracts.errors import SemanticError
from minidb.contracts.extensions import ExtensionPlan
from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta
from minidb.contracts.source import Position, Span


def span(offset: int = 0) -> Span:
    point = Position(offset, 1, offset + 1)
    return Span.at(point)


def student_table() -> TableMeta:
    return TableMeta(
        7,
        "student",
        (ColumnMeta("id", DataType.INT, 0), ColumnMeta("name", DataType.VARCHAR, 1), ColumnMeta("age", DataType.INT, 2)),
        3,
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
        raise AssertionError("UPDATE binding must not allocate")

    def register_table(self, table: TableMeta) -> None:
        self.write_called = True
        raise AssertionError("UPDATE binding must not register")

    def list_tables(self) -> list[TableMeta]:
        self.write_called = True
        raise AssertionError("UPDATE binding must not list")


def update(assignments: list[dict[str, object]], where: object | None = None) -> dict[str, object]:
    return {"kind": "update", "fields": {"table": "student", "assignments": assignments, "where": where}, "span": span()}


def assignment(name: str, expr: object, offset: int) -> dict[str, object]:
    return {"column": {"name": name, "span": span(offset)}, "expr": expr}


def test_update_binds_assignments_by_schema() -> None:
    stmt = update(
        [
            assignment("age", Literal(21, span(10)), 10),
            assignment("name", Literal("李四", span(20)), 20),
        ],
        BinaryExpr("=", Identifier("id", span(30)), Literal(1, span(32)), span(30)),
    )
    plan = build_update_plan(stmt, ReadOnlyCatalog(student_table()))
    assert isinstance(plan, ExtensionPlan)
    assert plan.feature == "update" and plan.version == 1
    payload = dict(plan.payload)
    assignments = payload["assignments"]
    assert [item["index"] for item in assignments] == [1, 2]
    assert payload["where"]["left"]["index"] == 0
    assert payload["child"]["type"] == "Filter"
    assert payload["child"]["child"]["type"] == "SeqScan"


def test_update_duplicate_assignment_rejected() -> None:
    stmt = update([assignment("age", Literal(20, span(10)), 10), assignment("AGE", Literal(21, span(20)), 20)])
    with pytest.raises(SemanticError) as raised:
        build_update_plan(stmt, ReadOnlyCatalog(student_table()))
    assert raised.value.code == "DUPLICATE_UPDATE_COLUMN"
    assert raised.value.span == span(20)


def test_update_type_and_unknown_column() -> None:
    with pytest.raises(SemanticError) as mismatch:
        build_update_plan(update([assignment("age", Literal("20", span(10)), 10)]), ReadOnlyCatalog(student_table()))
    assert mismatch.value.code == "TYPE_MISMATCH"
    assert mismatch.value.span == span(10)

    with pytest.raises(SemanticError) as unknown:
        build_update_plan(update([assignment("score", Literal(1, span(20)), 20)]), ReadOnlyCatalog(student_table()))
    assert unknown.value.code == "UNKNOWN_COLUMN"
    assert unknown.value.span == span(20)


def test_update_without_where_has_full_scan() -> None:
    plan = build_update_plan(update([assignment("age", Literal(0, span(10)), 10)]), ReadOnlyCatalog(student_table()))
    payload = dict(plan.payload)
    assert payload["where"] is None
    assert payload["child"]["type"] == "SeqScan"
    assert "Project" not in str(payload["child"])
    assert plan.version == 1


def test_update_column_swap_binding() -> None:
    table = TableMeta(8, "pair", (ColumnMeta("a", DataType.INT, 0), ColumnMeta("b", DataType.INT, 1)), 4)
    assignments = [
        {"column": {"name": "a", "span": span(1)}, "expr": Identifier("b", span(3))},
        {"column": {"name": "b", "span": span(5)}, "expr": Identifier("a", span(7))},
    ]
    bound = bind_assignments(assignments, table)
    assert [item["index"] for item in bound] == [0, 1]
    assert bound[0]["expr"]["index"] == 1
    assert bound[1]["expr"]["index"] == 0
    assert all(item["expr"]["dtype"] == "INT" for item in bound)
