from __future__ import annotations

import pytest

from minidb.compiler.optimizer import OptimizationReport, optimize
from minidb.compiler.rules import Rule, RuleOptimizer
from minidb.contracts.bound import BoundBinary, BoundColumn, BoundLiteral
from minidb.contracts.errors import OptimizationError
from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta
from minidb.contracts.plans import FilterPlan, ProjectPlan, SeqScanPlan
from minidb.contracts.source import Position, Span


def span(offset: int = 0) -> Span:
    point = Position(offset, 1, offset + 1)
    return Span.at(point)


def table() -> TableMeta:
    return TableMeta(
        7,
        "student",
        (ColumnMeta("id", DataType.INT, 0), ColumnMeta("age", DataType.INT, 1)),
        3,
    )


def predicate() -> BoundBinary:
    true = BoundBinary("=", BoundLiteral(1, DataType.INT, span(1)), BoundLiteral(1, DataType.INT, span(2)), DataType.BOOL, span(1))
    age = BoundBinary(">=", BoundColumn(1, "age", DataType.INT, span(3)), BoundLiteral(18, DataType.INT, span(4)), DataType.BOOL, span(3))
    return BoundBinary("AND", true, age, DataType.BOOL, span(1))


def demo_plan() -> ProjectPlan:
    scan = SeqScanPlan(table(), span())
    return ProjectPlan(FilterPlan(scan, predicate(), span()), (1,), ("age",), span())


def test_registered_rules_preserve_core_result() -> None:
    original = demo_plan()
    report = OptimizationReport()
    optimized = optimize(original, report)
    assert isinstance(optimized, ProjectPlan)
    assert isinstance(optimized.child, FilterPlan)
    assert isinstance(optimized.child.predicate, BoundBinary)
    assert optimized.child.predicate.op == ">="
    assert report.count("constant_comparison") >= 1
    assert report.count("boolean_simplify") >= 1
    assert original.child.predicate.op == "AND"


def test_rule_selection_is_explicit() -> None:
    report = OptimizationReport()
    optimized = optimize(demo_plan(), report, enabled_names={"constant_comparison"})
    assert isinstance(optimized, ProjectPlan)
    assert isinstance(optimized.child, FilterPlan)
    assert isinstance(optimized.child.predicate, BoundBinary)
    assert optimized.child.predicate.op == "AND"
    assert report.count("constant_comparison") >= 1
    assert report.count("boolean_simplify") == 0


def test_registration_is_deterministic() -> None:
    order: list[str] = []
    identity = demo_plan()

    def make_rule(name: str) -> Rule:
        return Rule(name, 10, lambda plan: (order.append(name) or plan))

    registry = RuleOptimizer()
    registry.register(make_rule("z_rule"))
    registry.register(make_rule("a_rule"))
    registry.run(identity)
    assert order == ["a_rule", "z_rule"]
    with pytest.raises(ValueError, match="重复规则名"):
        registry.register(make_rule("a_rule"))


def test_oscillation_hits_iteration_limit() -> None:
    base = SeqScanPlan(table(), span(0))
    alternate = SeqScanPlan(table(), span(1))

    def oscillate(plan):
        return alternate if plan == base else base

    registry = RuleOptimizer((Rule("oscillate", 1, oscillate),), max_rounds=3)
    with pytest.raises(OptimizationError) as raised:
        registry.run(base)
    assert raised.value.stage == "OPTIMIZATION"
    assert raised.value.code == "ITERATION_LIMIT"
    assert raised.value.context["rule"] == "oscillate"
    assert raised.value.context["max_rounds"] == 3
    assert len(registry.trace.records) == 3
