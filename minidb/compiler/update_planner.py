"""update/v1 扩展的 UPDATE 绑定与保留 RID 的逻辑计划。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from minidb.contracts.ast import Identifier
from minidb.contracts.bound import BoundExpr
from minidb.contracts.errors import SemanticError
from minidb.contracts.extensions import BoundExtension, ExtensionStatement, ExtensionPlan
from minidb.contracts.metadata import TableMeta
from minidb.contracts.source import Span, UNKNOWN_SPAN

from .semantic import bind_expression, require_predicate, resolve_column


def _error(code: str, message: str, span: Span) -> SemanticError:
    return SemanticError(code, message, span=span)


def _span(value: object, fallback: Span) -> Span:
    return value if isinstance(value, Span) else fallback


def _target(value: object, fallback: Span) -> tuple[str, Span]:
    if isinstance(value, Identifier):
        return value.name, value.span
    if isinstance(value, str):
        return value, fallback
    if isinstance(value, Mapping) and isinstance(value.get("name"), str):
        return value["name"], _span(value.get("span"), fallback)
    raise _error("INVALID_ASSIGNMENT", "UPDATE 赋值目标必须是列名", fallback)


def _assignment_parts(value: object, fallback: Span) -> tuple[object, object, Span]:
    if isinstance(value, Mapping):
        target = value.get("column", value.get("target"))
        expression = value.get("expr", value.get("value"))
        if target is None or expression is None:
            raise _error("INVALID_ASSIGNMENT", "UPDATE 赋值必须包含 column 和 expr", fallback)
        target_name, target_span = _target(target, fallback)
        return target_name, expression, target_span
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and len(value) == 2:
        target_name, target_span = _target(value[0], fallback)
        return target_name, value[1], target_span
    raise _error("INVALID_ASSIGNMENT", "UPDATE 赋值节点格式无效", fallback)


def _expr_payload(expr: BoundExpr) -> dict[str, object]:
    from minidb.contracts.bound import BoundBinary, BoundColumn, BoundLiteral, BoundUnary

    if isinstance(expr, BoundLiteral):
        return {"kind": "literal", "value": expr.value, "dtype": expr.dtype.value}
    if isinstance(expr, BoundColumn):
        return {"kind": "column", "name": expr.name, "index": expr.index, "dtype": expr.dtype.value}
    if isinstance(expr, BoundUnary):
        return {"kind": "unary", "op": expr.op, "operand": _expr_payload(expr.operand), "dtype": expr.dtype.value}
    if isinstance(expr, BoundBinary):
        return {"kind": "binary", "op": expr.op, "left": _expr_payload(expr.left), "right": _expr_payload(expr.right), "dtype": expr.dtype.value}
    raise TypeError(f"不支持的 Bound 表达式类型: {type(expr).__name__}")


def bind_assignments(assignments: Sequence[object], table: TableMeta, span: Span = UNKNOWN_SPAN) -> tuple[dict[str, object], ...]:
    """绑定全部 SET 项，拒绝重复目标并按目标 schema index 排序。"""

    bound: list[dict[str, object]] = []
    seen: set[int] = set()
    for assignment in assignments:
        name, expression, target_span = _assignment_parts(assignment, span)
        column = resolve_column(name, table, target_span)
        if column.index in seen:
            raise _error("DUPLICATE_UPDATE_COLUMN", f"UPDATE 重复赋值列 {column.name!r}", target_span)
        seen.add(column.index)
        bound_expr = bind_expression(expression, table)
        if bound_expr.dtype is not column.dtype:
            raise _error(
                "TYPE_MISMATCH",
                f"列 {column.name!r} 赋值类型不匹配，期望 {column.dtype.value}、实际 {bound_expr.dtype.value}",
                getattr(expression, "span", target_span),
            )
        bound.append({"index": column.index, "expr": _expr_payload(bound_expr)})
    return tuple(sorted(bound, key=lambda item: item["index"]))


def _update_input(value: Mapping[str, object] | ExtensionStatement | BoundExtension) -> tuple[Mapping[str, object], Span]:
    if isinstance(value, (ExtensionStatement, BoundExtension)):
        if value.feature != "update" or value.version != 1:
            raise _error("UNSUPPORTED_VERSION", "仅支持 update/v1", value.span)
        return value.payload, value.span
    if not isinstance(value, Mapping):
        raise _error("INVALID_UPDATE", "UPDATE 输入必须是 update/v1 节点对象", UNKNOWN_SPAN)
    return value, _span(value.get("span"), UNKNOWN_SPAN)


def validate_update(statement: Mapping[str, object] | ExtensionStatement | BoundExtension, catalog) -> tuple[TableMeta, tuple[dict[str, object], ...], BoundExpr | None, Span]:
    """校验 UPDATE 节点并返回表、排序赋值、WHERE 和语句 Span。"""

    payload, statement_span = _update_input(statement)
    if payload.get("kind") == "update":
        fields = payload.get("fields")
    else:
        fields = payload
    if not isinstance(fields, Mapping):
        raise _error("INVALID_UPDATE", "UPDATE 节点必须包含 fields 对象", statement_span)
    table_value = fields.get("table")
    table_name, table_span = _target(table_value, statement_span)
    try:
        table = catalog.get_table(table_name.casefold())
    except KeyError as error:
        raise _error("TABLE_NOT_FOUND", f"表 {table_name!r} 不存在", table_span) from error
    assignments = fields.get("assignments")
    if not isinstance(assignments, Sequence) or isinstance(assignments, (str, bytes, bytearray)):
        raise _error("INVALID_UPDATE", "UPDATE assignments 必须是数组", statement_span)
    bound_assignments = bind_assignments(assignments, table, statement_span)
    where_value = fields.get("where")
    where = None if where_value is None else require_predicate(bind_expression(where_value, table), _span(getattr(where_value, "span", None), statement_span))
    return table, bound_assignments, where, statement_span


def build_update_plan(statement: Mapping[str, object] | ExtensionStatement | BoundExtension, catalog) -> ExtensionPlan:
    """构造 update/v1 计划；child 保持 SeqScan/Filter 的 RID 路径。"""

    table, assignments, where, statement_span = validate_update(statement, catalog)
    child: dict[str, object] = {"type": "SeqScan", "table": table.name}
    where_payload = None
    if where is not None:
        where_payload = _expr_payload(where)
        child = {"type": "Filter", "predicate": where_payload, "child": child}
    payload = {
        "table": table.name,
        "assignments": list(assignments),
        "where": where_payload,
        "child": child,
    }
    return ExtensionPlan("update", 1, payload, statement_span)


__all__ = ["bind_assignments", "build_update_plan", "validate_update"]
