"""CREATE、INSERT 和 DELETE 逻辑计划的执行函数。"""

from __future__ import annotations

from minidb.contracts import (
    CatalogPort,
    CreateTablePlan,
    DeletePlan,
    ExecutionError,
    ExecutionResult,
    InsertPlan,
    Span,
    StorageIOError,
    StoragePort,
    StoredRow,
    TableMeta,
    TraceEvent,
)

from .operators import build_operator


def _io_error(code: str, action: str, table: str, error: OSError, span: Span) -> StorageIOError:
    return StorageIOError(
        code,
        f"{action}失败：{error}",
        span,
        {"table": table, "reason": str(error)},
    )


def execute_create(
    plan: CreateTablePlan,
    catalog: CatalogPort,
    storage: StoragePort,
) -> ExecutionResult:
    """先创建真实首页，再登记完整 TableMeta，最后刷新。"""

    if catalog.table_exists(plan.name):
        raise ExecutionError(
            "TABLE_ALREADY_EXISTS",
            f"表 {plan.name} 已存在",
            plan.span,
            {"table": plan.name},
        )
    table_id = catalog.allocate_table_id()
    try:
        first_page_id = storage.create_table(table_id, plan.columns)
    except OSError as error:
        raise _io_error("CREATE_TABLE_IO", "创建表页", plan.name, error, plan.span) from error
    table = TableMeta(table_id, plan.name, plan.columns, first_page_id)
    catalog.register_table(table)
    try:
        storage.flush_all()
    except OSError as error:
        raise _io_error("FLUSH_FAILED", "刷新建表结果", plan.name, error, plan.span) from error
    return ExecutionResult(
        affected_rows=0,
        message=f"已创建表 {plan.name}",
        trace=(
            TraceEvent(
                "EXECUTION",
                f"CREATE table={plan.name} table_id={table_id} first_page_id={first_page_id}",
            ),
        ),
    )


def execute_insert(plan: InsertPlan, storage: StoragePort) -> ExecutionResult:
    """插入已经按 schema 排序的值，并在成功刷新后报告一行。"""

    try:
        rid = storage.insert(plan.table, plan.values)
    except OSError as error:
        raise _io_error("INSERT_IO", "插入记录", plan.table.name, error, plan.span) from error
    try:
        storage.flush_all()
    except OSError as error:
        raise _io_error("FLUSH_FAILED", "刷新插入结果", plan.table.name, error, plan.span) from error
    return ExecutionResult(
        affected_rows=1,
        message="已插入 1 行",
        trace=(TraceEvent("EXECUTION", f"INSERT rid={rid.page_id}:{rid.slot_id}"),),
    )


def preview_delete(plan: DeletePlan, storage: StoragePort) -> tuple[StoredRow, ...]:
    """只读返回将被删除的行；不调用 mark_delete 或 flush。"""

    operator = build_operator(plan.child, storage)
    rows: list[StoredRow] = []
    try:
        operator.open()
        while (row := operator.next()) is not None:
            rows.append(row)
    finally:
        operator.close()
    return tuple(rows)


def execute_delete(plan: DeletePlan, storage: StoragePort) -> ExecutionResult:
    """按扫描产生的 RID 标记删除，只统计成功完成的标记。"""

    operator = build_operator(plan.child, storage)
    affected_rows = 0
    trace: list[TraceEvent] = []
    try:
        operator.open()
        while (row := operator.next()) is not None:
            try:
                storage.mark_delete(plan.table, row.rid)
            except OSError as error:
                raise StorageIOError(
                    "DELETE_IO",
                    f"标记删除失败：{error}",
                    plan.span,
                    {
                        "table": plan.table.name,
                        "page_id": row.rid.page_id,
                        "slot_id": row.rid.slot_id,
                        "reason": str(error),
                    },
                ) from error
            affected_rows += 1
            trace.append(TraceEvent("EXECUTION", f"DELETE rid={row.rid.page_id}:{row.rid.slot_id}"))
    finally:
        operator.close()
    try:
        storage.flush_all()
    except OSError as error:
        raise _io_error("FLUSH_FAILED", "刷新删除结果", plan.table.name, error, plan.span) from error
    trace.append(TraceEvent("EXECUTION", f"DELETE affected_rows={affected_rows}"))
    return ExecutionResult(
        affected_rows=affected_rows,
        message=f"已删除 {affected_rows} 行",
        trace=tuple(trace),
    )


__all__ = ["execute_create", "execute_delete", "execute_insert", "preview_delete"]
