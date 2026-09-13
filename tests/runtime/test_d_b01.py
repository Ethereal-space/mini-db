from __future__ import annotations

import pytest

from minidb.contracts import (
    BoundBinary,
    BoundColumn,
    BoundLiteral,
    BoundUnary,
    DataType,
    ExecutionError,
    UNKNOWN_SPAN,
)
from minidb.runtime.expression_evaluator import evaluate


def test_db01_columns() -> None:
    values = (7, "Tom", 22)
    expression = BoundColumn(2, "age", DataType.INT, UNKNOWN_SPAN)

    assert evaluate(expression, values) == 22
    assert values == (7, "Tom", 22)


def test_db01_predicate() -> None:
    age = BoundColumn(2, "age", DataType.INT, UNKNOWN_SPAN)
    name = BoundColumn(1, "name", DataType.VARCHAR, UNKNOWN_SPAN)
    adult = BoundBinary(
        ">=", age, BoundLiteral(18, DataType.INT, UNKNOWN_SPAN), DataType.BOOL, UNKNOWN_SPAN
    )
    is_tom = BoundBinary(
        "=", name, BoundLiteral("Tom", DataType.VARCHAR, UNKNOWN_SPAN), DataType.BOOL, UNKNOWN_SPAN
    )
    not_tom = BoundUnary("NOT", is_tom, DataType.BOOL, UNKNOWN_SPAN)
    predicate = BoundBinary("AND", adult, not_tom, DataType.BOOL, UNKNOWN_SPAN)

    tom_result = evaluate(predicate, (7, "Tom", 22))
    alice_result = evaluate(predicate, (8, "Alice", 20))

    assert tom_result is False
    assert alice_result is True
    assert type(tom_result) is bool
    assert type(alice_result) is bool


def test_db01_short_circuit() -> None:
    class BadExpr:
        calls = 0

        @property
        def span(self):
            self.calls += 1
            raise AssertionError("短路时不应访问右节点")

    bad = BadExpr()
    false_and_bad = BoundBinary(
        "AND", BoundLiteral(False, DataType.BOOL, UNKNOWN_SPAN), bad, DataType.BOOL, UNKNOWN_SPAN
    )
    true_or_bad = BoundBinary(
        "OR", BoundLiteral(True, DataType.BOOL, UNKNOWN_SPAN), bad, DataType.BOOL, UNKNOWN_SPAN
    )

    assert evaluate(false_and_bad, ()) is False
    assert evaluate(true_or_bad, ()) is True
    assert bad.calls == 0


def test_db01_bad_column() -> None:
    expression = BoundColumn(3, "age", DataType.INT, UNKNOWN_SPAN)

    with pytest.raises(ExecutionError) as captured:
        evaluate(expression, (7, "Tom", 22))

    assert captured.value.stage == "EXECUTION"
    assert captured.value.code == "COLUMN_INDEX_OUT_OF_RANGE"
    assert captured.value.span == UNKNOWN_SPAN
    assert captured.value.context["index"] == 3
    assert "行宽度" in captured.value.message


def test_db01_no_coercion() -> None:
    mixed = BoundBinary(
        "=",
        BoundLiteral(1, DataType.INT, UNKNOWN_SPAN),
        BoundLiteral("1", DataType.VARCHAR, UNKNOWN_SPAN),
        DataType.BOOL,
        UNKNOWN_SPAN,
    )
    not_integer = BoundUnary(
        "NOT", BoundLiteral(1, DataType.INT, UNKNOWN_SPAN), DataType.BOOL, UNKNOWN_SPAN
    )

    with pytest.raises(ExecutionError) as mixed_error:
        evaluate(mixed, ())
    with pytest.raises(ExecutionError) as not_error:
        evaluate(not_integer, ())

    assert mixed_error.value.code == "COMPARISON_TYPE_MISMATCH"
    assert not_error.value.code == "BOOLEAN_REQUIRED"


def test_db01_comparison_alias() -> None:
    expression = BoundBinary(
        "==",
        BoundLiteral(9, DataType.INT, UNKNOWN_SPAN),
        BoundLiteral(9, DataType.INT, UNKNOWN_SPAN),
        DataType.BOOL,
        UNKNOWN_SPAN,
    )

    assert evaluate(expression, ()) is True


@pytest.mark.parametrize(
    ("operator", "expected"),
    [("=", False), ("!=", True), ("<", True), ("<=", True), (">", False), (">=", False)],
)
def test_db01_all_integer_comparisons(operator: str, expected: bool) -> None:
    expression = BoundBinary(
        operator,
        BoundLiteral(2, DataType.INT, UNKNOWN_SPAN),
        BoundLiteral(3, DataType.INT, UNKNOWN_SPAN),
        DataType.BOOL,
        UNKNOWN_SPAN,
    )

    assert evaluate(expression, ()) is expected


def test_db01_varchar_ordering_and_unknown_node_are_rejected() -> None:
    ordering = BoundBinary(
        "<",
        BoundLiteral("a", DataType.VARCHAR, UNKNOWN_SPAN),
        BoundLiteral("b", DataType.VARCHAR, UNKNOWN_SPAN),
        DataType.BOOL,
        UNKNOWN_SPAN,
    )

    with pytest.raises(ExecutionError) as ordering_error:
        evaluate(ordering, ())
    with pytest.raises(ExecutionError) as node_error:
        evaluate(object(), ())

    assert ordering_error.value.code == "UNSUPPORTED_VARCHAR_COMPARISON"
    assert node_error.value.code == "UNKNOWN_EXPRESSION"
