from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from minidb.contracts import (
    BinaryExpr,
    BoundColumn,
    BoundInsert,
    BoundLiteral,
    BufferStats,
    CORE_KEYWORDS,
    ColumnDef,
    ColumnMeta,
    CreateTableStmt,
    DataType,
    DeletePlan,
    ExecutionResult,
    ExtensionPlan,
    FilterPlan,
    Identifier,
    InsertPlan,
    Literal,
    Position,
    RID,
    SeqScanPlan,
    Span,
    StoredRow,
    TableMeta,
    TokenKind,
    thaw_json_value,
)


SPAN = Span(Position(0, 1, 1), Position(1, 1, 2))
COLUMNS = (
    ColumnMeta("id", DataType.INT, 0),
    ColumnMeta("name", DataType.VARCHAR, 1),
)
TABLE = TableMeta(1, "student", COLUMNS, 2)


def test_position_and_span_are_validated_and_frozen() -> None:
    assert SPAN.length == 1
    assert Span.covering(SPAN, Span(Position(3, 1, 4), Position(5, 1, 6))).length == 5
    with pytest.raises(ValueError):
        Position(-1, 1, 1)
    with pytest.raises(ValueError):
        Span(Position(2, 1, 3), Position(1, 1, 2))
    with pytest.raises(FrozenInstanceError):
        SPAN.start = Position(1, 1, 2)  # type: ignore[misc]


def test_metadata_uses_canonical_names_and_core_types() -> None:
    assert TABLE.columns[1].ordinal == 1
    with pytest.raises(ValueError):
        Identifier("Student", SPAN)
    with pytest.raises(ValueError):
        ColumnMeta("flag", DataType.BOOL, 0)
    with pytest.raises(ValueError):
        TableMeta(0, "catalog", COLUMNS, 1)
    with pytest.raises(ValueError):
        TableMeta(2, "student", (COLUMNS[1], COLUMNS[0]), 3)


def test_keyword_maps_are_read_only() -> None:
    assert CORE_KEYWORDS["select"] is TokenKind.SELECT
    with pytest.raises(TypeError):
        CORE_KEYWORDS["select"] = TokenKind.IDENTIFIER  # type: ignore[index]


def test_ast_preserves_structure_and_rejects_core_bool() -> None:
    identifier = Identifier("student", SPAN)
    column = ColumnDef(Identifier("id", SPAN), "INT", SPAN)
    statement = CreateTableStmt(identifier, (column,), SPAN)
    expression = BinaryExpr("=", Identifier("id", SPAN), Literal(1, SPAN), SPAN)
    assert statement.columns == (column,)
    assert expression.right.value == 1
    with pytest.raises(TypeError):
        Literal(True, SPAN)
    with pytest.raises(ValueError):
        ColumnDef(Identifier("x", SPAN), "FLOAT", SPAN)


def test_bound_and_plan_insert_width_and_types() -> None:
    assert BoundLiteral(True, DataType.BOOL, SPAN).value is True
    assert BoundColumn(0, "id", DataType.INT, SPAN).index == 0
    assert BoundInsert(TABLE, (1, "张三"), SPAN).values[1] == "张三"
    assert InsertPlan(TABLE, (1, "张三"), SPAN).table == TABLE
    with pytest.raises(TypeError):
        BoundLiteral(True, DataType.INT, SPAN)
    with pytest.raises(ValueError):
        BoundInsert(TABLE, (1,), SPAN)
    with pytest.raises(TypeError):
        BoundInsert(TABLE, (True, "张三"), SPAN)
    with pytest.raises(ValueError):
        InsertPlan(TABLE, (1,), SPAN)


def test_delete_plan_requires_same_table_scan_chain() -> None:
    scan = SeqScanPlan(TABLE, SPAN)
    predicate = BoundLiteral(True, DataType.BOOL, SPAN)
    filtered = FilterPlan(scan, predicate, SPAN)
    assert DeletePlan(TABLE, filtered, SPAN).child == filtered
    other = TableMeta(2, "other", COLUMNS, 3)
    with pytest.raises(ValueError):
        DeletePlan(TABLE, SeqScanPlan(other, SPAN), SPAN)
    with pytest.raises(TypeError):
        DeletePlan(TABLE, InsertPlan(TABLE, (1, "x"), SPAN), SPAN)


def test_results_enforce_shape_and_nonnegative_counts() -> None:
    row = StoredRow(RID(2, 0), (1, "张三"))
    result = ExecutionResult(columns=("id", "name"), rows=(row.values,))
    assert result.rows == ((1, "张三"),)
    with pytest.raises(ValueError):
        ExecutionResult(columns=("id",), rows=((1, "extra"),))
    with pytest.raises(ValueError):
        BufferStats(hits=-1)


def test_extension_payload_is_deeply_copied_and_read_only() -> None:
    original = {"child": {"kind": "scan"}, "indices": [0, 1]}
    plan = ExtensionPlan("update", 1, original, SPAN)
    original["child"]["kind"] = "mutated"  # type: ignore[index]
    original["indices"].append(2)  # type: ignore[union-attr]
    assert thaw_json_value(plan.payload) == {
        "child": {"kind": "scan"},
        "indices": [0, 1],
    }
    assert json.loads(json.dumps(plan.payload)) == {
        "child": {"kind": "scan"},
        "indices": [0, 1],
    }
    with pytest.raises(TypeError):
        plan.payload["new"] = 1  # type: ignore[index]
    with pytest.raises(ValueError):
        ExtensionPlan("UPDATE", 1, {}, SPAN)
