"""arithmetic/v1 扩展的绑定、校验与常量折叠。"""

from __future__ import annotations

from collections.abc import Mapping

from minidb.contracts.errors import SemanticError
from minidb.contracts.extensions import BoundExtension as ContractBoundExtension, ExtensionStatement
from minidb.contracts.metadata import DataType
from minidb.contracts.source import Span, UNKNOWN_SPAN

from .semantic import INT32_MAX, INT32_MIN, resolve_column


ARITHMETIC_OPS = frozenset({"+", "-", "*"})


def _error(code: str, message: str, span: Span) -> SemanticError:
    return SemanticError(code, message, span=span)


def _node_span(node: Mapping[str, object], fallback: Span) -> Span:
    value = node.get("span", fallback)
    return value if isinstance(value, Span) else fallback


def validate_payload(payload: Mapping[str, object], span: Span = UNKNOWN_SPAN) -> None:
    """验证 arithmetic/v1 节点的 kind/fields 结构。"""

    kind = payload.get("kind")
    fields = payload.get("fields")
    if kind not in {"literal", "column", "binary"} or not isinstance(fields, Mapping):
        raise _error("INVALID_PAYLOAD", "arithmetic/v1 节点必须包含合法 kind 和 fields", span)
    required = {"value"} if kind == "literal" else {"name"} if kind == "column" else {"op", "left", "right"}
    missing = sorted(key for key in required if key not in fields)
    if missing:
        raise _error("INVALID_PAYLOAD", f"arithmetic/v1 缺少字段 {', '.join(missing)}", span)
    if kind == "binary":
        for child_name in ("left", "right"):
            child = fields[child_name]
            if not isinstance(child, Mapping):
                raise _error("INVALID_PAYLOAD", f"arithmetic/v1 字段 {child_name} 必须是对象", span)


def _raw_node(value: object, fallback: Span) -> tuple[Mapping[str, object], Span]:
    if not isinstance(value, Mapping):
        raise _error("INVALID_PAYLOAD", "arithmetic/v1 子节点必须是对象", fallback)
    node_span = _node_span(value, fallback)
    validate_payload(value, node_span)
    return value, node_span


def _bind_node(value: object, table, fallback: Span) -> dict[str, object]:
    node, node_span = _raw_node(value, fallback)
    kind = node["kind"]
    fields = node["fields"]
    if kind == "literal":
        literal_value = fields["value"]
        if isinstance(literal_value, bool) or not isinstance(literal_value, int):
            raise _error("TYPE_MISMATCH", "算术表达式字面量必须为 INT", node_span)
        if not INT32_MIN <= literal_value <= INT32_MAX:
            raise _error("INT32_OUT_OF_RANGE", f"INT 值 {literal_value!r} 超出 INT32 范围", node_span)
        return {"kind": "literal", "fields": {"value": literal_value, "dtype": DataType.INT.value}}
    if kind == "column":
        name = fields["name"]
        if not isinstance(name, str):
            raise _error("TYPE_MISMATCH", "算术列引用名称必须为字符串", node_span)
        column = resolve_column(name, table, node_span)
        if column.dtype is not DataType.INT:
            raise _error("TYPE_MISMATCH", f"算术列 {column.name!r} 必须为 INT", node_span)
        return {"kind": "column", "fields": {"name": column.name, "index": column.index, "dtype": DataType.INT.value}}
    op = fields["op"]
    if op not in ARITHMETIC_OPS:
        raise _error("UNSUPPORTED_OPERATOR", f"arithmetic/v1 不支持运算符 {op!r}", node_span)
    left = _bind_node(fields["left"], table, node_span)
    right = _bind_node(fields["right"], table, node_span)
    if left["kind"] == "literal" and right["kind"] == "literal":
        _checked_calculate(left["fields"]["value"], op, right["fields"]["value"], node_span)
    return {"kind": "binary", "fields": {"op": op, "left": left, "right": right, "dtype": DataType.INT.value}}


def _as_payload(value: object, span: Span | None) -> tuple[Mapping[str, object], Span]:
    if isinstance(value, ExtensionStatement):
        if value.feature != "arithmetic" or value.version != 1:
            raise _error("UNSUPPORTED_VERSION", "仅支持 arithmetic/v1", value.span)
        return value.payload, value.span
    if isinstance(value, ContractBoundExtension):
        if value.feature != "arithmetic" or value.version != 1:
            raise _error("UNSUPPORTED_VERSION", "仅支持 arithmetic/v1", value.span)
        return value.payload, value.span
    if not isinstance(value, Mapping):
        raise _error("INVALID_PAYLOAD", "arithmetic/v1 输入必须是节点对象", span or UNKNOWN_SPAN)
    actual_span = span or _node_span(value, UNKNOWN_SPAN)
    validate_payload(value, actual_span)
    return value, actual_span


def bind_arithmetic(node: Mapping[str, object] | ContractBoundExtension, table, span: Span | None = None) -> ContractBoundExtension:
    """绑定 arithmetic/v1 节点，输出冻结 BoundExtension。"""

    payload, node_span = _as_payload(node, span)
    bound_payload = _bind_node(payload, table, node_span)
    return ContractBoundExtension("arithmetic", 1, bound_payload, node_span)


def _checked_calculate(left: int, op: str, right: int, span: Span) -> int:
    value = left + right if op == "+" else left - right if op == "-" else left * right
    if not INT32_MIN <= value <= INT32_MAX:
        raise _error("INT32_OUT_OF_RANGE", f"算术结果 {value!r} 超出 INT32 范围", span)
    return value


def _fold_node(node: Mapping[str, object], span: Span) -> dict[str, object]:
    kind = node["kind"]
    fields = node["fields"]
    if kind != "binary":
        return {"kind": kind, "fields": dict(fields)}
    op = fields["op"]
    left = _fold_node(fields["left"], span)
    right = _fold_node(fields["right"], span)
    left_fields = left["fields"]
    right_fields = right["fields"]
    if left["kind"] == "literal" and right["kind"] == "literal":
        value = _checked_calculate(left_fields["value"], op, right_fields["value"], span)
        return {"kind": "literal", "fields": {"value": value, "dtype": DataType.INT.value}}
    return {"kind": "binary", "fields": {"op": op, "left": left, "right": right, "dtype": DataType.INT.value}}


def fold_arithmetic(bound: ContractBoundExtension) -> ContractBoundExtension:
    """自底向上折叠纯常量 arithmetic/v1 子树，并检查每个结果范围。"""

    payload, node_span = _as_payload(bound, None)
    return ContractBoundExtension("arithmetic", 1, _fold_node(payload, node_span), node_span)


__all__ = ["bind_arithmetic", "fold_arithmetic", "validate_payload"]
