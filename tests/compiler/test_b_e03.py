from __future__ import annotations

from copy import deepcopy

import pytest

from minidb.compiler.arithmetic import bind_arithmetic, fold_arithmetic
from minidb.contracts.bound import BoundBinary, BoundColumn, BoundLiteral
from minidb.contracts.errors import SemanticError
from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta
from minidb.contracts.source import Position, Span


def span(offset: int = 0) -> Span:
    point = Position(offset, 1, offset + 1)
    return Span.at(point)


def student_table() -> TableMeta:
    return TableMeta(7, "student", (ColumnMeta("age", DataType.INT, 0),), 3)


def node(op: str, left: dict[str, object], right: dict[str, object], offset: int = 0) -> dict[str, object]:
    return {"kind": "binary", "fields": {"op": op, "left": left, "right": right}, "span": span(offset)}


def integer(value: object, offset: int = 0) -> dict[str, object]:
    return {"kind": "literal", "fields": {"value": value}, "span": span(offset)}


def column(name: str, offset: int = 0) -> dict[str, object]:
    return {"kind": "column", "fields": {"name": name}, "span": span(offset)}


def payload_node(bound) -> dict[str, object]:
    return dict(bound.payload)


def test_fold_course_example() -> None:
    arithmetic = bind_arithmetic(node("+", integer(10, 4), integer(8, 7), 4), student_table())
    folded = fold_arithmetic(arithmetic)
    fields = payload_node(folded)["fields"]
    assert fields["value"] == 18
    assert fields["dtype"] == "INT"
    assert folded.span == span(4)
    comparison = BoundBinary(">", BoundColumn(0, "age", DataType.INT, span(0)), BoundLiteral(18, DataType.INT, span(4)), DataType.BOOL, span(0))
    assert [age > comparison.right.value for age in (17, 18, 19)] == [False, False, True]


def test_fold_nested_arithmetic() -> None:
    raw = node("-", node("*", node("+", integer(2, 1), integer(3, 3), 1), integer(4, 5), 1), integer(1, 7), 1)
    original = deepcopy(raw)
    bound = bind_arithmetic(raw, student_table())
    folded = fold_arithmetic(bound)
    assert payload_node(folded)["fields"]["value"] == 19
    assert raw == original
    assert fold_arithmetic(folded) == folded


def test_arithmetic_rejects_overflow_and_types() -> None:
    with pytest.raises(SemanticError) as overflow:
        bind_arithmetic(node("+", integer(2_147_483_647, 2), integer(1, 14), 2), student_table())
    assert overflow.value.code == "INT32_OUT_OF_RANGE"
    assert overflow.value.span == span(2)

    with pytest.raises(SemanticError) as mismatch:
        bind_arithmetic(node("+", integer("1", 5), integer(2, 9), 5), student_table())
    assert mismatch.value.code == "TYPE_MISMATCH"
    assert mismatch.value.span == span(5)

    with pytest.raises(SemanticError) as division:
        bind_arithmetic(node("/", integer(8, 12), integer(2, 14), 12), student_table())
    assert division.value.code == "UNSUPPORTED_OPERATOR"
    assert division.value.span == span(12)


def test_arithmetic_predicate_equivalence() -> None:
    arithmetic = fold_arithmetic(bind_arithmetic(node("+", integer(10), integer(8)), student_table()))
    threshold = payload_node(arithmetic)["fields"]["value"]
    before = [age > (10 + 8) for age in (17, 18, 19)]
    after = [age > threshold for age in (17, 18, 19)]
    assert before == after == [False, False, True]
