

from __future__ import annotations

from minidb.contracts.ast import BinaryExpr, ColumnDef, CreateTableStmt, DeleteStmt, Identifier, InsertStmt, Literal, SelectStmt, UnaryExpr
from minidb.contracts.bound import BoundBinary, BoundColumn, BoundCreate, BoundDelete, BoundExpr, BoundInsert, BoundLiteral, BoundSelect, BoundUnary
from minidb.contracts.errors import SemanticError
from minidb.contracts.metadata import (
    ColumnMeta,
    DataType,
    StoredValue,
    TableMeta,
    normalize_identifier,
)
from minidb.contracts.ports import CatalogPort
from minidb.contracts.source import Span, UNKNOWN_SPAN


INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
VARCHAR_MAX_BYTES = 255


def _error(
    code: str,
    message: str,
    span: Span,
    *,
    context: dict[str, object] | None = None,
) -> SemanticError:
    return SemanticError(code, message, span=span, context=context)


def _normalized_name(name: str, span: Span) -> str:
    try:
        return normalize_identifier(name)
    except (TypeError, ValueError) as error:
        raise _error("INVALID_IDENTIFIER", f"非法标识符 {name!r}", span) from error


def analyze_create(stmt: CreateTableStmt, catalog: CatalogPort) -> BoundCreate:
    """Validate CREATE metadata and produce a schema-ordered BoundCreate."""

    table_name = _normalized_name(stmt.name.name, stmt.name.span)
    if catalog.table_exists(table_name):
        raise _error(
            "DUPLICATE_TABLE_NAME",
            f"表 {table_name!r} 已存在",
            stmt.name.span,
            context={"table": table_name},
        )
    if not stmt.columns:
        raise _error("EMPTY_COLUMNS", "CREATE TABLE 至少需要一列", stmt.span)

    columns: list[ColumnMeta] = []
    seen_names: set[str] = set()
    for ordinal, column in enumerate(stmt.columns):
        column_name = _normalized_name(column.name.name, column.name.span)
        if column_name in seen_names:
            raise _error(
                "DUPLICATE_COLUMN_NAME",
                f"重复列名 {column_name!r}",
                column.name.span,
                context={"column": column_name},
            )
        seen_names.add(column_name)
        try:
            dtype = DataType(column.dtype_name)
        except ValueError as error:
            raise _error(
                "UNSUPPORTED_COLUMN_TYPE",
                f"不支持的列类型 {column.dtype_name!r}",
                column.span,
            ) from error
        if dtype not in {DataType.INT, DataType.VARCHAR}:
            raise _error(
                "UNSUPPORTED_COLUMN_TYPE",
                f"不支持的存储列类型 {column.dtype_name!r}",
                column.span,
            )
        columns.append(ColumnMeta(column_name, dtype, ordinal))

    return BoundCreate(table_name, tuple(columns), stmt.span)


def _get_table(stmt: InsertStmt, catalog: CatalogPort) -> TableMeta:
    table_name = _normalized_name(stmt.table.name, stmt.table.span)
    try:
        return catalog.get_table(table_name)
    except KeyError as error:
        raise _error(
            "TABLE_NOT_FOUND",
            f"表 {table_name!r} 不存在",
            stmt.table.span,
            context={"table": table_name},
        ) from error


def validate_literal(value: object, dtype: DataType, span: Span) -> StoredValue:
    """Validate one literal without performing implicit conversions."""

    if dtype is DataType.INT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise _error(
                "TYPE_MISMATCH",
                f"值 {value!r} 不是 INT",
                span,
                context={"value": value, "expected": DataType.INT.value},
            )
        if not INT32_MIN <= value <= INT32_MAX:
            raise _error(
                "INT32_OUT_OF_RANGE",
                f"INT 值 {value!r} 超出 INT32 范围",
                span,
                context={"value": value},
            )
        return value

    if dtype is DataType.VARCHAR:
        if not isinstance(value, str):
            raise _error(
                "TYPE_MISMATCH",
                f"值 {value!r} 不是 VARCHAR",
                span,
                context={"value": value, "expected": DataType.VARCHAR.value},
            )
        byte_length = len(value.encode("utf-8"))
        if byte_length > VARCHAR_MAX_BYTES:
            raise _error(
                "VARCHAR_TOO_LONG",
                f"VARCHAR 值长度为 {byte_length} 字节，超过 255 字节",
                span,
                context={"bytes": byte_length},
            )
        return value

    raise _error(
        "UNSUPPORTED_COLUMN_TYPE",
        f"不支持的存储列类型 {dtype.value!r}",
        span,
    )


def bind_insert_columns(
    stmt: InsertStmt,
    table: TableMeta,
) -> tuple[StoredValue, ...]:
    """Validate INSERT columns and return values in table schema order."""

    schema = {column.name: column for column in table.columns}
    if stmt.columns is None:
        if len(stmt.values) != len(table.columns):
            raise _error(
                "VALUE_COUNT_MISMATCH",
                f"INSERT 值数量错误，期望 {len(table.columns)} 实得 {len(stmt.values)}",
                stmt.span,
                context={"expected": len(table.columns), "actual": len(stmt.values)},
            )
        return tuple(
            validate_literal(literal.value, column.dtype, literal.span)
            for literal, column in zip(stmt.values, table.columns, strict=True)
        )

    if len(stmt.columns) != len(stmt.values):
        raise _error(
            "VALUE_COUNT_MISMATCH",
            f"INSERT 列和值数量不一致，列 {len(stmt.columns)} 个、值 {len(stmt.values)} 个",
            stmt.span,
        )

    input_columns: list[tuple[str, ColumnMeta, Literal]] = []
    seen_names: set[str] = set()
    for column_identifier, literal in zip(stmt.columns, stmt.values, strict=True):
        column_name = _normalized_name(column_identifier.name, column_identifier.span)
        if column_name in seen_names:
            raise _error(
                "DUPLICATE_INSERT_COLUMN",
                f"INSERT 列名重复 {column_name!r}",
                column_identifier.span,
            )
        seen_names.add(column_name)
        try:
            column = schema[column_name]
        except KeyError as error:
            raise _error(
                "UNKNOWN_COLUMN",
                f"列 {column_name!r} 不存在",
                column_identifier.span,
                context={"column": column_name, "table": table.name},
            ) from error
        input_columns.append((column_name, column, literal))

    if seen_names != set(schema):
        missing = sorted(set(schema) - seen_names)
        raise _error(
            "INCOMPLETE_COLUMN_SET",
            f"INSERT 必须提供完整列集合，缺少 {', '.join(missing)}",
            stmt.span,
            context={"missing": missing},
        )

    values_by_name = {
        column_name: validate_literal(literal.value, column.dtype, literal.span)
        for column_name, column, literal in input_columns
    }
    return tuple(values_by_name[column.name] for column in table.columns)


def analyze_insert(stmt: InsertStmt, catalog: CatalogPort) -> BoundInsert:
    """Bind an INSERT statement against Catalog metadata."""

    table = _get_table(stmt, catalog)
    values = bind_insert_columns(stmt, table)
    return BoundInsert(table, values, stmt.span)


def _get_named_table(name: Identifier, catalog: CatalogPort) -> TableMeta:
    table_name = _normalized_name(name.name, name.span)
    try:
        return catalog.get_table(table_name)
    except KeyError as error:
        raise _error("TABLE_NOT_FOUND", f"表 {table_name!r} 不存在", name.span, context={"table": table_name}) from error


def resolve_column(name: str, table: TableMeta, span: Span) -> BoundColumn:
    """将列名绑定到表 schema 中稳定的 ordinal、类型和源位置。"""

    column_name = _normalized_name(name, span)
    for column in table.columns:
        if column.name == column_name:
            return BoundColumn(column.ordinal, column.name, column.dtype, span)
    raise _error(
        "UNKNOWN_COLUMN",
        f"列 {column_name!r} 不存在于表 {table.name!r}",
        span,
        context={"column": column_name, "table": table.name},
    )


def bind_projection(columns: tuple[Identifier, ...] | None, table: TableMeta) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """展开星号或按用户顺序绑定显式投影，保留重复项。"""

    if columns is None:
        return tuple(column.ordinal for column in table.columns), tuple(column.name for column in table.columns)
    bound = tuple(resolve_column(column.name, table, column.span) for column in columns)
    return tuple(column.index for column in bound), tuple(column.name for column in bound)


COMPARISON_OPERATORS = frozenset({"=", "!=", "<", "<=", ">", ">="})
LOGICAL_OPERATORS = frozenset({"AND", "OR"})


def infer_binary(op: str, left_type: DataType, right_type: DataType, span: Span) -> DataType:
    if op in COMPARISON_OPERATORS:
        if left_type is not right_type:
            raise _error("TYPE_MISMATCH", f"比较 {op!r} 两侧类型不一致: {left_type.value} 与 {right_type.value}", span)
        if left_type is DataType.INT or (left_type is DataType.VARCHAR and op in {"=", "!="}):
            return DataType.BOOL
        raise _error("UNSUPPORTED_OPERATOR", f"VARCHAR 不支持比较运算符 {op!r}", span)
    if op in LOGICAL_OPERATORS:
        if left_type is not DataType.BOOL or right_type is not DataType.BOOL:
            raise _error("BOOLEAN_REQUIRED", f"逻辑运算 {op!r} 两侧必须为 BOOL", span)
        return DataType.BOOL
    raise _error("UNSUPPORTED_OPERATOR", f"不支持的二元运算符 {op!r}", span)


def _literal_dtype(value: object, span: Span) -> DataType:
    if isinstance(value, bool):
        raise _error("UNSUPPORTED_LITERAL", "核心语义不支持 BOOL 字面量", span)
    if isinstance(value, int):
        return DataType.INT
    if isinstance(value, str):
        return DataType.VARCHAR
    if isinstance(value, float):
        raise _error("UNSUPPORTED_FLOAT", "核心语义不支持 FLOAT 字面量", span)
    raise _error("UNSUPPORTED_LITERAL", f"核心语义不支持字面量类型 {type(value).__name__}", span)


def bind_expression(expr: object, table: TableMeta) -> BoundExpr:
    if isinstance(expr, Literal):
        dtype = _literal_dtype(expr.value, expr.span)
        return BoundLiteral(expr.value, dtype, expr.span)
    if isinstance(expr, Identifier):
        return resolve_column(expr.name, table, expr.span)
    if isinstance(expr, UnaryExpr):
        operand = bind_expression(expr.operand, table)
        if expr.op.upper() != "NOT":
            raise _error("UNSUPPORTED_OPERATOR", f"不支持的一元运算符 {expr.op!r}", expr.span)
        if operand.dtype is not DataType.BOOL:
            raise _error("BOOLEAN_REQUIRED", "NOT 的操作数必须为 BOOL", expr.operand.span)
        return BoundUnary(expr.op, operand, DataType.BOOL, expr.span)
    if isinstance(expr, BinaryExpr):
        left = bind_expression(expr.left, table)
        right = bind_expression(expr.right, table)
        return BoundBinary(expr.op, left, right, infer_binary(expr.op, left.dtype, right.dtype, expr.span), expr.span)
    raise _error("INVALID_EXPRESSION", f"不支持的表达式节点 {type(expr).__name__}", UNKNOWN_SPAN)


def require_predicate(bound: BoundExpr, span: Span) -> BoundExpr:
    if bound.dtype is not DataType.BOOL:
        raise _error("BOOLEAN_REQUIRED", f"WHERE 表达式必须为 BOOL，实际为 {bound.dtype.value}", span)
    return bound


def bind_select(stmt: SelectStmt, catalog: CatalogPort) -> BoundSelect:
    table = _get_named_table(stmt.table, catalog)
    indices, names = bind_projection(stmt.columns, table)
    where = None if stmt.where is None else require_predicate(bind_expression(stmt.where, table), stmt.where.span)
    return BoundSelect(table, indices, names, where, stmt.span)


def bind_delete(stmt: DeleteStmt, catalog: CatalogPort) -> BoundDelete:
    table = _get_named_table(stmt.table, catalog)
    where = None if stmt.where is None else require_predicate(bind_expression(stmt.where, table), stmt.where.span)
    return BoundDelete(table, where, stmt.span)


def analyze_select(stmt: SelectStmt, catalog: CatalogPort) -> BoundSelect:
    return bind_select(stmt, catalog)


def analyze_delete(stmt: DeleteStmt, catalog: CatalogPort) -> BoundDelete:
    return bind_delete(stmt, catalog)


__all__ = [
    "INT32_MAX",
    "INT32_MIN",
    "VARCHAR_MAX_BYTES",
    "analyze_create",
    "analyze_insert",
    "analyze_delete",
    "analyze_select",
    "bind_delete",
    "bind_expression",
    "bind_insert_columns",
    "bind_projection",
    "bind_select",
    "infer_binary",
    "require_predicate",
    "resolve_column",
    "validate_literal",
]
