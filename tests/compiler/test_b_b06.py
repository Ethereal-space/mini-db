from __future__ import annotations

import pytest

from minidb.compiler.optimizer import OptimizationReport, fold_constants, optimize
from minidb.compiler.plan_formatter import format_plan
from minidb.compiler.planner import Compiler
from minidb.contracts.ast import BinaryExpr, Identifier, Literal, SelectStmt
from minidb.contracts.bound import BoundBinary, BoundColumn, BoundExpr, BoundLiteral, BoundUnary
from minidb.contracts.errors import SemanticError
from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta
from minidb.contracts.plans import FilterPlan, ProjectPlan, SeqScanPlan
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


def literal(value: int | str, dtype: DataType, offset: int = 0) -> BoundLiteral:
    return BoundLiteral(value, dtype, span(offset))


def comparison(op: str, left: BoundExpr, right: BoundExpr, offset: int = 0) -> BoundBinary:
    return BoundBinary(op, left, right, DataType.BOOL, span(offset))


def age_at_least_18() -> BoundBinary:
    return comparison(">=", BoundColumn(2, "age", DataType.INT, span(20)), literal(18, DataType.INT, 21), 20)


def and_expr(left: BoundExpr, right: BoundExpr, offset: int = 0) -> BoundBinary:
    return BoundBinary("AND", left, right, DataType.BOOL, span(offset))


def reference_value(expr: BoundExpr, row: tuple[int, str, int]) -> bool | int | str:
    if isinstance(expr, BoundLiteral):
        return expr.value
    if isinstance(expr, BoundColumn):
        return row[expr.index]
    if isinstance(expr, BoundUnary):
        assert expr.op == "NOT"
        return not reference_value(expr.operand, row)
    left = reference_value(expr.left, row)
    right = reference_value(expr.right, row)
    if expr.op == "AND":
        return bool(left and right)
    if expr.op == "OR":
        return bool(left or right)
    return {
        "=": left == right,
        "!=": left != right,
        "<": left < right,
        "<=": left <= right,
        ">": left > right,
        ">=": left >= right,
    }[expr.op]


class FakeCatalog:
    def __init__(self, table: TableMeta) -> None:
        self.table = table

    def table_exists(self, name: str) -> bool:
        return name == self.table.name

    def get_table(self, name: str) -> TableMeta:
        if name != self.table.name:
            raise AssertionError(name)
        return self.table


def test_constant_comparison_folding() -> None:
    cases = (
        (comparison("=", literal(1, DataType.INT, 1), literal(1, DataType.INT, 2), 1), True),
        (comparison("<", literal(1, DataType.INT, 3), literal(0, DataType.INT, 4), 3), False),
        (comparison("!=", literal("a", DataType.VARCHAR, 5), literal("b", DataType.VARCHAR, 6), 5), True),
    )
    for expression, expected in cases:
        report = OptimizationReport()
        folded = fold_constants(expression, report)
        assert isinstance(folded, BoundLiteral)
        assert folded.value is expected
        assert folded.dtype is DataType.BOOL
        assert folded.span == expression.span
        assert report.count("constant_comparison") == 1


def test_two_rules_transform_demo_plan() -> None:
    table = student_table()
    predicate = and_expr(comparison("=", literal(1, DataType.INT), literal(1, DataType.INT)), age_at_least_18())
    original = ProjectPlan(
        FilterPlan(SeqScanPlan(table, span()), predicate, span()),
        (1,),
        ("name",),
        span(),
    )
    report = OptimizationReport()
    optimized = optimize(original, report)
    assert isinstance(optimized, ProjectPlan)
    assert isinstance(optimized.child, FilterPlan)
    assert optimized.child.predicate == age_at_least_18()
    assert report.count("constant_comparison") >= 1
    assert report.count("boolean_simplify") >= 1
    assert isinstance(original.child, FilterPlan)
    assert isinstance(original.child.predicate, BoundBinary)
    assert original.child.predicate.op == "AND"


def test_true_filter_removed_false_filter_kept() -> None:
    table = student_table()
    scan = SeqScanPlan(table, span())
    true_plan = optimize(FilterPlan(scan, comparison("=", literal(1, DataType.INT), literal(1, DataType.INT)), span()))
    false_plan = optimize(FilterPlan(scan, comparison("=", literal(1, DataType.INT), literal(0, DataType.INT)), span()))
    assert true_plan == scan
    assert isinstance(false_plan, FilterPlan)
    assert isinstance(false_plan.predicate, BoundLiteral)
    assert false_plan.predicate.value is False
    assert type(false_plan.child) is SeqScanPlan
    assert "EmptyPlan" not in type(false_plan).__name__


def test_optimizer_equivalence_and_idempotence() -> None:
    table = student_table()
    name_is_tom = comparison("=", BoundColumn(1, "name", DataType.VARCHAR, span(30)), literal("Tom", DataType.VARCHAR, 31), 30)
    predicate = and_expr(
        and_expr(comparison("=", literal(1, DataType.INT), literal(1, DataType.INT)), age_at_least_18()),
        BoundUnary("NOT", name_is_tom, DataType.BOOL, span(32)),
    )
    original = FilterPlan(SeqScanPlan(table, span()), predicate, span())
    before = tuple(row[0] for row in ((1, "Alice", 20), (2, "Bob", 17), (3, "Tom", 22), (4, "张三", 21)) if reference_value(predicate, row))
    optimized = optimize(original)
    assert isinstance(optimized, FilterPlan)
    after = tuple(row[0] for row in ((1, "Alice", 20), (2, "Bob", 17), (3, "Tom", 22), (4, "张三", 21)) if reference_value(optimized.predicate, row))
    assert before == after == (1, 4)
    assert optimize(optimized) == optimized
    assert original == FilterPlan(SeqScanPlan(table, span()), predicate, span())


def test_plan_format_is_stable_and_semantic_errors_survive() -> None:
    table = student_table()
    plan = ProjectPlan(FilterPlan(SeqScanPlan(table, span()), age_at_least_18(), span()), (1,), ("name",), span())
    rendered = format_plan(plan)
    assert rendered == format_plan(plan)
    assert all(token in rendered for token in ("Project", "Filter", "SeqScan", "age#2"))
    missing = SelectStmt(
        Identifier("student", span()),
        None,
        BinaryExpr(
            "AND",
            BinaryExpr("=", Literal(1, span(1)), Literal(0, span(2)), span(1)),
            BinaryExpr("=", Identifier("missing", span(3)), Literal(1, span(4)), span(3)),
            span(1),
        ),
        span(),
    )
    with pytest.raises(SemanticError) as raised:
        Compiler().compile(missing, FakeCatalog(table))
    assert raised.value.stage == "SEMANTIC"
    assert raised.value.span == span(3)
