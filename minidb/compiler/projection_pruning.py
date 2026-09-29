"""SELECT 投影需求分析与安全的 projection_pruning/v1 快照。"""

from __future__ import annotations

from collections.abc import Iterable

from minidb.contracts.bound import BoundBinary, BoundColumn, BoundExpr, BoundLiteral, BoundUnary
from minidb.contracts.errors import PlanningError
from minidb.contracts.extensions import ExtensionPlan
from minidb.contracts.plans import DeletePlan, FilterPlan, PlanNode, ProjectPlan, SeqScanPlan
from minidb.contracts.source import Span


def _expression_columns(expr: BoundExpr) -> set[int]:
    if isinstance(expr, BoundColumn):
        return {expr.index}
    if isinstance(expr, BoundLiteral):
        return set()
    if isinstance(expr, BoundUnary):
        return _expression_columns(expr.operand)
    return _expression_columns(expr.left) | _expression_columns(expr.right)


def _plan_columns(plan: PlanNode, demanded: set[int] | None = None) -> set[int]:
    if isinstance(plan, SeqScanPlan):
        return set() if demanded is None else set(demanded)
    if isinstance(plan, FilterPlan):
        return _expression_columns(plan.predicate) | _plan_columns(plan.child, demanded)
    if isinstance(plan, ProjectPlan):
        output = set(plan.indices) if demanded is None else {
            plan.indices[index] for index in demanded if 0 <= index < len(plan.indices)
        }
        if demanded is not None and any(index < 0 or index >= len(plan.indices) for index in demanded):
            raise PlanningError("INVALID_PROJECTION_INDEX", "Project 的需求索引超出投影宽度", plan.span)
        return _plan_columns(plan.child, output)
    if isinstance(plan, DeletePlan):
        return _plan_columns(plan.child, demanded)
    return set()


def required_columns(value: PlanNode | BoundExpr) -> tuple[int, ...]:
    """返回表达式或计划所需的原始列序号，结果稳定排序且不重复。"""

    if isinstance(value, (BoundColumn, BoundLiteral, BoundUnary, BoundBinary)):
        return tuple(sorted(_expression_columns(value)))
    return tuple(sorted(_plan_columns(value)))


def compose_projects(
    inner_indices: tuple[int, ...],
    inner_names: tuple[str, ...],
    outer_indices: tuple[int, ...],
    outer_names: tuple[str, ...],
) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """将外层投影索引映射到内层输出，保留重复项和外层名称。"""

    if len(inner_indices) != len(inner_names) or len(outer_indices) != len(outer_names):
        raise PlanningError("INVALID_PROJECTION_WIDTH", "相邻 Project 的索引与名称宽度不一致")
    try:
        indices = tuple(inner_indices[index] for index in outer_indices)
    except IndexError as error:
        raise PlanningError("INVALID_PROJECTION_INDEX", "Project 外层索引超出内层输出宽度") from error
    return indices, outer_names


def _rebind_expression(expr: BoundExpr, index_map: dict[int, int]) -> BoundExpr:
    if isinstance(expr, BoundLiteral):
        return expr
    if isinstance(expr, BoundColumn):
        try:
            index = index_map[expr.index]
        except KeyError as error:
            raise PlanningError("MISSING_REQUIRED_COLUMN", f"列 {expr.name!r} 未包含在裁剪布局中", expr.span) from error
        return BoundColumn(index, expr.name, expr.dtype, expr.span)
    if isinstance(expr, BoundUnary):
        return BoundUnary(expr.op, _rebind_expression(expr.operand, index_map), expr.dtype, expr.span)
    return BoundBinary(
        expr.op,
        _rebind_expression(expr.left, index_map),
        _rebind_expression(expr.right, index_map),
        expr.dtype,
        expr.span,
    )


def _remove_true_and_merge(plan: PlanNode) -> PlanNode:
    if isinstance(plan, FilterPlan):
        child = _remove_true_and_merge(plan.child)
        if isinstance(plan.predicate, BoundLiteral) and plan.predicate.value is True:
            return child
        return FilterPlan(child, plan.predicate, plan.span)
    if isinstance(plan, ProjectPlan):
        child = _remove_true_and_merge(plan.child)
        if isinstance(child, ProjectPlan):
            indices, names = compose_projects(child.indices, child.names, plan.indices, plan.names)
            return ProjectPlan(child.child, indices, names, plan.span)
        return ProjectPlan(child, plan.indices, plan.names, plan.span)
    return plan


def _find_scan(plan: PlanNode) -> SeqScanPlan | None:
    if isinstance(plan, SeqScanPlan):
        return plan
    if isinstance(plan, (FilterPlan, ProjectPlan)):
        return _find_scan(plan.child)
    return None


def _find_filter(plan: PlanNode) -> FilterPlan | None:
    if isinstance(plan, FilterPlan):
        return plan
    if isinstance(plan, ProjectPlan):
        return _find_filter(plan.child)
    return None


def _plan_child_payload(plan: PlanNode) -> dict[str, object]:
    if isinstance(plan, SeqScanPlan):
        return {"type": "SeqScan", "table": plan.table.name}
    if isinstance(plan, FilterPlan):
        return {"type": "Filter", "predicate": _expression_payload(plan.predicate), "child": _plan_child_payload(plan.child)}
    if isinstance(plan, ProjectPlan):
        return {"type": "Project", "indices": list(plan.indices), "names": list(plan.names), "child": _plan_child_payload(plan.child)}
    raise PlanningError("UNSUPPORTED_PRUNING_PLAN", f"不支持的裁剪子计划 {type(plan).__name__}")


def _expression_payload(expr: BoundExpr) -> dict[str, object]:
    if isinstance(expr, BoundLiteral):
        return {"kind": "literal", "dtype": expr.dtype.value, "value": expr.value}
    if isinstance(expr, BoundColumn):
        return {"kind": "column", "name": expr.name, "index": expr.index, "dtype": expr.dtype.value}
    if isinstance(expr, BoundUnary):
        return {"kind": "unary", "op": expr.op, "operand": _expression_payload(expr.operand), "dtype": expr.dtype.value}
    return {
        "kind": "binary",
        "op": expr.op,
        "left": _expression_payload(expr.left),
        "right": _expression_payload(expr.right),
        "dtype": expr.dtype.value,
    }


def prune_plan(plan: PlanNode) -> PlanNode:
    """为可安全分析的 SELECT 生成裁剪快照；DELETE 保持原计划。"""

    if isinstance(plan, DeletePlan):
        return plan
    normalized = _remove_true_and_merge(plan)
    if not isinstance(normalized, ProjectPlan):
        return normalized
    scan = _find_scan(normalized)
    if scan is None:
        raise PlanningError("MISSING_SCAN", "投影裁剪计划缺少 SeqScan")
    columns = required_columns(normalized)
    index_map = {old: new for new, old in enumerate(columns)}
    predicate = _find_filter(normalized)
    rewritten_predicate = None if predicate is None else _expression_payload(_rebind_expression(predicate.predicate, index_map))
    projection_indices = [index_map[index] for index in normalized.indices]
    payload: dict[str, object] = {
        "columns": list(columns),
        "index_map": {str(old): new for old, new in index_map.items()},
        "child": {"type": "SeqScan", "table": scan.table.name},
        "projection": {"indices": projection_indices, "names": list(normalized.names)},
        "predicate": rewritten_predicate,
    }
    return ExtensionPlan("projection_pruning", 1, payload, normalized.span)


__all__ = ["compose_projects", "prune_plan", "required_columns"]
