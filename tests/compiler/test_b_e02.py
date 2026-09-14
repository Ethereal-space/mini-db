from __future__ import annotations

import pytest

from minidb.compiler.projection_pruning import compose_projects, prune_plan, required_columns
from minidb.contracts.bound import BoundBinary, BoundColumn, BoundExpr, BoundLiteral
from minidb.contracts.extensions import ExtensionPlan
from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta
from minidb.contracts.plans import DeletePlan, FilterPlan, ProjectPlan, SeqScanPlan
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


def age_predicate(index: int = 2) -> BoundBinary:
    return BoundBinary(">=", BoundColumn(index, "age", DataType.INT, span(4)), BoundLiteral(18, DataType.INT, span(5)), DataType.BOOL, span(4))


def select_plan() -> ProjectPlan:
    table = student_table()
    scan = SeqScanPlan(table, span())
    return ProjectPlan(FilterPlan(scan, age_predicate(), span()), (1,), ("name",), span())


def evaluate_bound(expr: BoundExpr, row: tuple[object, ...]) -> bool | object:
    if isinstance(expr, BoundLiteral):
        return expr.value
    if isinstance(expr, BoundColumn):
        return row[expr.index]
    left = evaluate_bound(expr.left, row)
    right = evaluate_bound(expr.right, row)
    return {"=": left == right, "!=": left != right, "<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[expr.op]


def evaluate_snapshot(payload: dict[str, object], rows: tuple[tuple[object, ...], ...]) -> list[tuple[object, ...]]:
    columns = payload["columns"]
    projection = payload["projection"]
    predicate = payload["predicate"]
    result: list[tuple[object, ...]] = []
    for row in rows:
        compact = tuple(row[int(index)] for index in columns)
        left = compact[int(predicate["left"]["index"])]
        if left < predicate["right"]["value"]:
            continue
        result.append(tuple(compact[int(index)] for index in projection["indices"]))
    return result


def test_required_columns_include_predicate() -> None:
    assert required_columns(select_plan()) == (1, 2)


def test_project_composition_preserves_duplicates() -> None:
    indices, names = compose_projects((2, 0, 1), ("age", "id", "name"), (2, 2, 0), ("name", "name", "age"))
    assert indices == (1, 1, 2)
    assert names == ("name", "name", "age")


def test_pruning_rebinds_every_index() -> None:
    result = prune_plan(select_plan())
    assert isinstance(result, ExtensionPlan)
    assert result.feature == "projection_pruning"
    assert result.version == 1
    assert result.payload["columns"] == [1, 2]
    assert result.payload["index_map"] == {"1": 0, "2": 1}
    assert result.payload["predicate"]["left"]["index"] == 1
    assert result.payload["projection"]["indices"] == [0]


def test_pruned_plan_matches_reference() -> None:
    rows = ((1, "Alice", 20), (2, "Bob", 17))
    original = select_plan()
    original_rows = [tuple(row[index] for index in original.indices) for row in rows if evaluate_bound(original.child.predicate, row)]
    snapshot = prune_plan(original)
    assert isinstance(snapshot, ExtensionPlan)
    pruned_rows = evaluate_snapshot(dict(snapshot.payload), rows)
    assert original_rows == pruned_rows == [("Alice",)]

    delete = DeletePlan(student_table(), FilterPlan(SeqScanPlan(student_table(), span()), age_predicate(), span()), span())
    assert prune_plan(delete) == delete
