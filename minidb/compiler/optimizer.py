"""核心 Bound 表达式与 Logical Plan 的不可变规则优化。"""

from __future__ import annotations

from dataclasses import dataclass, field

from minidb.contracts.bound import BoundBinary, BoundColumn, BoundExpr, BoundLiteral, BoundUnary
from minidb.contracts.extensions import ExtensionPlan
from minidb.contracts.metadata import DataType
from minidb.contracts.plans import (
    CreateTablePlan,
    DeletePlan,
    FilterPlan,
    InsertPlan,
    PlanNode,
    ProjectPlan,
    SeqScanPlan,
)
from minidb.contracts.source import Span

from .plan_formatter import format_expression, format_plan


@dataclass(frozen=True, slots=True)
class RuleHit:
    rule_name: str
    before: str
    after: str


@dataclass(slots=True)
class OptimizationReport:
    hits: list[RuleHit] = field(default_factory=list)

    def record(self, rule_name: str, before: str, after: str) -> None:
        self.hits.append(RuleHit(rule_name, before, after))

    def count(self, rule_name: str) -> int:
        return sum(hit.rule_name == rule_name for hit in self.hits)

    @property
    def counts(self) -> dict[str, int]:
        return {name: self.count(name) for name in sorted({hit.rule_name for hit in self.hits})}


_COMPARISON_OPS = frozenset({"=", "!=", "<", "<=", ">", ">="})


def _compare(left: int | str, op: str, right: int | str) -> bool:
    if op == "=":
        return left == right
    if op == "!=":
        return left != right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    return left >= right


def fold_constants(expr: BoundExpr, report: OptimizationReport | None = None) -> BoundExpr:
    """递归折叠同类型 INT/VARCHAR 常量比较，保留比较表达式 Span。"""

    active_report = report if report is not None else OptimizationReport()
    if isinstance(expr, (BoundLiteral, BoundColumn)):
        return expr
    if isinstance(expr, BoundUnary):
        operand = fold_constants(expr.operand, active_report)
        return BoundUnary(expr.op, operand, expr.dtype, expr.span) if operand != expr.operand else expr
    left = fold_constants(expr.left, active_report)
    right = fold_constants(expr.right, active_report)
    rebuilt = BoundBinary(expr.op, left, right, expr.dtype, expr.span) if (left, right) != (expr.left, expr.right) else expr
    if (
        isinstance(rebuilt, BoundBinary)
        and rebuilt.op in _COMPARISON_OPS
        and isinstance(rebuilt.left, BoundLiteral)
        and isinstance(rebuilt.right, BoundLiteral)
        and rebuilt.left.dtype is rebuilt.right.dtype
        and rebuilt.left.dtype in {DataType.INT, DataType.VARCHAR}
    ):
        result = BoundLiteral(_compare(rebuilt.left.value, rebuilt.op, rebuilt.right.value), DataType.BOOL, rebuilt.span)
        active_report.record("constant_comparison", format_expression(rebuilt), format_expression(result))
        return result
    return rebuilt


def _bool_literal(value: bool, span: Span) -> BoundLiteral:
    return BoundLiteral(value, DataType.BOOL, span)


def simplify_boolean(expr: BoundExpr, report: OptimizationReport | None = None) -> BoundExpr:
    """递归应用核心 BOOL 恒等式，并为改写结果重新构造节点。"""

    active_report = report if report is not None else OptimizationReport()
    if isinstance(expr, (BoundLiteral, BoundColumn)):
        return expr
    if isinstance(expr, BoundUnary):
        operand = simplify_boolean(expr.operand, active_report)
        rebuilt = BoundUnary(expr.op, operand, expr.dtype, expr.span) if operand != expr.operand else expr
        if rebuilt.op == "NOT" and isinstance(rebuilt.operand, BoundLiteral) and rebuilt.operand.dtype is DataType.BOOL:
            result = _bool_literal(not rebuilt.operand.value, rebuilt.span)
            active_report.record("boolean_simplify", format_expression(rebuilt), format_expression(result))
            return result
        return rebuilt
    left = simplify_boolean(expr.left, active_report)
    right = simplify_boolean(expr.right, active_report)
    rebuilt = BoundBinary(expr.op, left, right, expr.dtype, expr.span) if (left, right) != (expr.left, expr.right) else expr
    if rebuilt.op not in {"AND", "OR"}:
        return rebuilt
    result: BoundExpr | None = None
    if isinstance(left, BoundLiteral) and left.dtype is DataType.BOOL and isinstance(right, BoundLiteral) and right.dtype is DataType.BOOL:
        result = _bool_literal(bool(left.value and right.value) if rebuilt.op == "AND" else bool(left.value or right.value), rebuilt.span)
    elif rebuilt.op == "AND" and isinstance(left, BoundLiteral) and left.dtype is DataType.BOOL and left.value:
        result = right
    elif rebuilt.op == "AND" and isinstance(right, BoundLiteral) and right.dtype is DataType.BOOL and right.value:
        result = left
    elif rebuilt.op == "OR" and isinstance(left, BoundLiteral) and left.dtype is DataType.BOOL and not left.value:
        result = right
    elif rebuilt.op == "OR" and isinstance(right, BoundLiteral) and right.dtype is DataType.BOOL and not right.value:
        result = left
    elif rebuilt.op == "AND" and isinstance(left, BoundLiteral) and left.dtype is DataType.BOOL and not left.value:
        result = left
    elif rebuilt.op == "OR" and isinstance(left, BoundLiteral) and left.dtype is DataType.BOOL and left.value:
        result = left
    if result is not None:
        active_report.record("boolean_simplify", format_expression(rebuilt), format_expression(result))
        return result
    return rebuilt


def optimize(plan: PlanNode, report: OptimizationReport | None = None) -> PlanNode:
    """重建计划树并优化其中表达式；原计划及其子节点保持不变。"""

    active_report = report if report is not None else OptimizationReport()
    if isinstance(plan, FilterPlan):
        child = optimize(plan.child, active_report)
        predicate = simplify_boolean(fold_constants(plan.predicate, active_report), active_report)
        if isinstance(predicate, BoundLiteral) and predicate.dtype is DataType.BOOL and predicate.value:
            active_report.record("filter_true_removed", format_plan(plan), format_plan(child))
            return child
        return FilterPlan(child, predicate, plan.span)
    if isinstance(plan, ProjectPlan):
        return ProjectPlan(optimize(plan.child, active_report), plan.indices, plan.names, plan.span)
    if isinstance(plan, DeletePlan):
        return DeletePlan(plan.table, optimize(plan.child, active_report), plan.span)
    if isinstance(plan, (SeqScanPlan, CreateTablePlan, InsertPlan, ExtensionPlan)):
        return plan
    raise TypeError(f"不支持的 Plan 类型: {type(plan).__name__}")


__all__ = ["OptimizationReport", "RuleHit", "fold_constants", "optimize", "simplify_boolean"]
