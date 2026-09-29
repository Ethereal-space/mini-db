"""为核心 Logical Plan 和 Bound 表达式提供稳定的可读文本。"""

from __future__ import annotations

from minidb.contracts.bound import BoundBinary, BoundColumn, BoundExpr, BoundLiteral, BoundUnary
from minidb.contracts.plans import (
    CreateTablePlan,
    DeletePlan,
    FilterPlan,
    InsertPlan,
    PlanNode,
    ProjectPlan,
    SeqScanPlan,
)


def format_expression(expr: BoundExpr) -> str:
    """格式化 Bound 表达式，不包含对象地址或其他运行时信息。"""

    if isinstance(expr, BoundLiteral):
        value = repr(expr.value) if expr.dtype.value == "VARCHAR" else str(expr.value)
        return f"{value}:{expr.dtype.value}"
    if isinstance(expr, BoundColumn):
        return f"{expr.name}#{expr.index}:{expr.dtype.value}"
    if isinstance(expr, BoundUnary):
        return f"({expr.op} {format_expression(expr.operand)})"
    if isinstance(expr, BoundBinary):
        return f"({format_expression(expr.left)} {expr.op} {format_expression(expr.right)})"
    raise TypeError(f"不支持的 Bound 表达式类型: {type(expr).__name__}")


def format_plan(plan: PlanNode) -> str:
    """按固定字段顺序输出计划树；缩进表示 child 关系。"""

    lines: list[str] = []

    def visit(node: PlanNode, depth: int) -> None:
        indent = "  " * depth
        if isinstance(node, SeqScanPlan):
            lines.append(f"{indent}SeqScan(table={node.table.name})")
            return
        if isinstance(node, FilterPlan):
            lines.append(f"{indent}Filter(predicate={format_expression(node.predicate)})")
            visit(node.child, depth + 1)
            return
        if isinstance(node, ProjectPlan):
            lines.append(f"{indent}Project(indices={node.indices}, names={node.names})")
            visit(node.child, depth + 1)
            return
        if isinstance(node, DeletePlan):
            lines.append(f"{indent}Delete(table={node.table.name})")
            visit(node.child, depth + 1)
            return
        if isinstance(node, CreateTablePlan):
            columns = tuple((column.name, column.dtype.value, column.ordinal) for column in node.columns)
            lines.append(f"{indent}CreateTable(name={node.name}, columns={columns})")
            return
        if isinstance(node, InsertPlan):
            lines.append(f"{indent}Insert(table={node.table.name}, values={node.values})")
            return
        lines.append(f"{indent}{type(node).__name__}(feature={node.feature}, version={node.version}, payload={dict(node.payload)})")

    visit(plan, 0)
    return "\n".join(lines)


__all__ = ["format_expression", "format_plan"]
