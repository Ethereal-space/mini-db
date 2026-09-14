from __future__ import annotations

import pytest

from minidb.compiler.semantic import bind_expression, infer_binary, require_predicate
from minidb.contracts.ast import BinaryExpr, Identifier, Literal, UnaryExpr
from minidb.contracts.bound import BoundBinary, BoundColumn, BoundLiteral, BoundUnary
from minidb.contracts.errors import SemanticError
from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta
from minidb.contracts.source import Position, Span


def span(offset: int) -> Span:
    position = Position(offset=offset, line=1, column=offset + 1)
    return Span.at(position)


def identifier(name: str, offset: int) -> Identifier:
    return Identifier(name=name, span=span(offset))


def literal(value: object, offset: int) -> Literal:
    return Literal(value=value, span=span(offset))


def binary(op: str, left: object, right: object, offset: int) -> BinaryExpr:
    return BinaryExpr(op=op, left=left, right=right, span=span(offset))


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


def test_integer_comparison_matrix() -> None:
    operators = ("=", "!=", "<", "<=", ">", ">=")

    for offset, operator in enumerate(operators):
        expression = binary(operator, literal(1, offset), literal(2, offset + 1), offset)
        bound = bind_expression(expression, student_table())

        assert isinstance(bound, BoundBinary)
        assert bound.dtype is DataType.BOOL
        assert bound.op == operator
        assert bound.span == expression.span
        assert isinstance(bound.left, BoundLiteral)
        assert isinstance(bound.right, BoundLiteral)
        assert bound.left.dtype is DataType.INT
        assert bound.right.dtype is DataType.INT
        assert bound.left.span == expression.left.span
        assert bound.right.span == expression.right.span
        assert expression.left == literal(1, offset)


def test_string_equality_and_type_mismatch() -> None:
    table = student_table()
    assert require_predicate(
        bind_expression(binary("=", literal("张三", 1), literal("张三", 2), 0), table),
        span(0),
    ).dtype is DataType.BOOL
    assert require_predicate(
        bind_expression(binary("!=", literal("a", 3), literal("b", 4), 2), table),
        span(2),
    ).dtype is DataType.BOOL

    with pytest.raises(SemanticError, match="INT.*VARCHAR|VARCHAR.*INT"):
        bind_expression(binary("=", identifier("age", 5), literal("18", 6), 5), table)
    with pytest.raises(SemanticError, match="VARCHAR.*<|不支持.*<"):
        bind_expression(binary("<", identifier("name", 7), literal("z", 8), 7), table)


def test_not_preserves_comparison_subtree() -> None:
    expression = binary(
        "OR",
        UnaryExpr(
            op="NOT",
            operand=binary("=", identifier("age", 1), literal(18, 2), 1),
            span=span(1),
        ),
        binary(
            "AND",
            binary(">", identifier("age", 3), literal(20, 4), 3),
            binary("!=", identifier("name", 5), literal("Tom", 6), 5),
            3,
        ),
        0,
    )

    bound = bind_expression(expression, student_table())

    assert isinstance(bound, BoundBinary)
    assert bound.op == "OR"
    assert bound.dtype is DataType.BOOL
    assert isinstance(bound.left, BoundUnary)
    assert bound.left.op == "NOT"
    assert isinstance(bound.left.operand, BoundBinary)
    assert bound.left.operand.op == "="
    assert isinstance(bound.right, BoundBinary)
    assert bound.right.op == "AND"
    assert isinstance(bound.right.left, BoundBinary)
    assert isinstance(bound.right.right, BoundBinary)
    assert bound.right.left.dtype is DataType.BOOL
    assert bound.right.right.dtype is DataType.BOOL
    assert isinstance(bound.left.operand.left, BoundColumn)
    assert isinstance(bound.right.left.left, BoundColumn)
    assert bound.left.operand.left.index == 2
    assert bound.right.left.left.index == 2


def test_where_rejects_non_boolean_and_float() -> None:
    table = student_table()

    with pytest.raises(SemanticError, match="WHERE.*BOOL|必须为 BOOL"):
        require_predicate(bind_expression(identifier("age", 10), table), span(10))
    with pytest.raises(SemanticError, match="BOOL"):
        bind_expression(
            binary("AND", literal(1, 11), binary("=", identifier("age", 12), literal(18, 13), 12), 11),
            table,
        )
    with pytest.raises(SemanticError, match="FLOAT|浮点"):
        bind_expression(binary("=", identifier("age", 14), literal(3.14, 15), 14), table)


def test_binding_checks_unreachable_branch() -> None:
    expression = binary(
        "AND",
        binary("=", literal(1, 1), literal(0, 2), 1),
        binary("=", identifier("missing_column", 3), literal(1, 4), 3),
        0,
    )

    with pytest.raises(SemanticError, match="missing_column") as raised:
        bind_expression(expression, student_table())

    assert raised.value.stage == "SEMANTIC"
    assert raised.value.span == expression.right.left.span
