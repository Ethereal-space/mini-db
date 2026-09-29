"""对已经完成名称绑定和类型检查的表达式求值。"""

from __future__ import annotations

from typing import Final

from minidb.contracts import (
    BoundBinary,
    BoundColumn,
    BoundExpr,
    BoundLiteral,
    BoundUnary,
    DataType,
    ExecutionError,
    ScalarValue,
    Span,
)


_COMPARISON_ALIASES: Final[dict[str, str]] = {"==": "=", "<>": "!="}
_INTEGER_COMPARISONS: Final[frozenset[str]] = frozenset({"=", "!=", "<", "<=", ">", ">="})
_VARCHAR_COMPARISONS: Final[frozenset[str]] = frozenset({"=", "!="})


def _span_of(expr: object) -> Span | None:
    span = getattr(expr, "span", None)
    return span if isinstance(span, Span) else None


def _execution_error(
    code: str,
    message: str,
    expr: object,
    **context: object,
) -> ExecutionError:
    return ExecutionError(code, message, _span_of(expr), context)


def _matches_dtype(value: object, dtype: DataType) -> bool:
    if dtype is DataType.BOOL:
        return isinstance(value, bool)
    if dtype is DataType.INT:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, str)


def _require_bool(value: ScalarValue, expr: object, *, role: str) -> bool:
    if not isinstance(value, bool):
        raise _execution_error(
            "BOOLEAN_REQUIRED",
            f"{role}必须产生 BOOL，实际为 {type(value).__name__}",
            expr,
            role=role,
            actual_type=type(value).__name__,
        )
    return value


def evaluate(expr: BoundExpr, values: tuple[int | str, ...]) -> ScalarValue:
    """使用 schema 顺序的行值递归求值，不查询 Catalog 或解析 SQL。"""

    if isinstance(expr, BoundLiteral):
        return expr.value
    if isinstance(expr, BoundColumn):
        if expr.index >= len(values):
            raise _execution_error(
                "COLUMN_INDEX_OUT_OF_RANGE",
                f"列下标 {expr.index} 超出行宽度 {len(values)}",
                expr,
                index=expr.index,
                row_width=len(values),
                column=expr.name,
            )
        value = values[expr.index]
        if not _matches_dtype(value, expr.dtype):
            raise _execution_error(
                "COLUMN_TYPE_MISMATCH",
                f"列 {expr.name} 的运行时值不符合 {expr.dtype.value}",
                expr,
                column=expr.name,
                expected_type=expr.dtype.value,
                actual_type=type(value).__name__,
            )
        return value
    if isinstance(expr, BoundUnary):
        return evaluate_unary(expr, values)
    if isinstance(expr, BoundBinary):
        return evaluate_binary(expr, values)
    raise _execution_error(
        "UNKNOWN_EXPRESSION",
        f"不支持的绑定表达式节点 {type(expr).__name__}",
        expr,
        node_type=type(expr).__name__,
    )


def evaluate_unary(expr: BoundUnary, values: tuple[int | str, ...]) -> bool:
    """求值核心一元运算；目前只允许 BOOL 上的 NOT。"""

    operator = expr.op.upper()
    if operator != "NOT":
        raise _execution_error(
            "UNSUPPORTED_UNARY_OPERATOR",
            f"不支持一元运算符 {expr.op!r}",
            expr,
            operator=expr.op,
        )
    if expr.dtype is not DataType.BOOL:
        raise _execution_error(
            "INVALID_EXPRESSION_TYPE",
            "NOT 表达式的结果类型必须是 BOOL",
            expr,
            actual_type=expr.dtype.value,
        )
    operand = _require_bool(evaluate(expr.operand, values), expr.operand, role="NOT 操作数")
    return not operand


def evaluate_binary(expr: BoundBinary, values: tuple[int | str, ...]) -> bool:
    """按白名单求值比较和短路布尔运算。"""

    operator = _COMPARISON_ALIASES.get(expr.op, expr.op).upper()
    if operator in {"AND", "OR"}:
        if expr.dtype is not DataType.BOOL:
            raise _execution_error(
                "INVALID_EXPRESSION_TYPE",
                f"{operator} 表达式的结果类型必须是 BOOL",
                expr,
                actual_type=expr.dtype.value,
            )
        left = _require_bool(evaluate(expr.left, values), expr.left, role=f"{operator} 左操作数")
        if operator == "AND" and not left:
            return False
        if operator == "OR" and left:
            return True
        return _require_bool(evaluate(expr.right, values), expr.right, role=f"{operator} 右操作数")

    if operator not in _INTEGER_COMPARISONS:
        raise _execution_error(
            "UNSUPPORTED_BINARY_OPERATOR",
            f"不支持二元运算符 {expr.op!r}",
            expr,
            operator=expr.op,
        )
    if expr.dtype is not DataType.BOOL:
        raise _execution_error(
            "INVALID_EXPRESSION_TYPE",
            "比较表达式的结果类型必须是 BOOL",
            expr,
            actual_type=expr.dtype.value,
        )

    left_dtype = getattr(expr.left, "dtype", None)
    right_dtype = getattr(expr.right, "dtype", None)
    if left_dtype is not right_dtype or left_dtype not in {DataType.INT, DataType.VARCHAR}:
        raise _execution_error(
            "COMPARISON_TYPE_MISMATCH",
            "比较两侧必须具有相同的 INT 或 VARCHAR 类型",
            expr,
            left_type=getattr(left_dtype, "value", repr(left_dtype)),
            right_type=getattr(right_dtype, "value", repr(right_dtype)),
        )
    if left_dtype is DataType.VARCHAR and operator not in _VARCHAR_COMPARISONS:
        raise _execution_error(
            "UNSUPPORTED_VARCHAR_COMPARISON",
            f"VARCHAR 不支持比较运算符 {operator}",
            expr,
            operator=operator,
        )

    left = evaluate(expr.left, values)
    right = evaluate(expr.right, values)
    if not _matches_dtype(left, left_dtype) or not _matches_dtype(right, right_dtype):
        raise _execution_error(
            "COMPARISON_VALUE_TYPE_MISMATCH",
            "比较值不符合已绑定的类型",
            expr,
            expected_type=left_dtype.value,
        )

    if operator == "=":
        return left == right
    if operator == "!=":
        return left != right
    if operator == "<":
        return left < right  # type: ignore[operator]
    if operator == "<=":
        return left <= right  # type: ignore[operator]
    if operator == ">":
        return left > right  # type: ignore[operator]
    return left >= right  # type: ignore[operator]


__all__ = ["evaluate", "evaluate_binary", "evaluate_unary"]
