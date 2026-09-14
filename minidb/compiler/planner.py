"""成员 B 的逻辑计划生成实现位置，对应任务 B-B05。"""

from __future__ import annotations

from collections.abc import Callable

from minidb.compiler.semantic import (
    analyze_create,
    analyze_delete,
    analyze_insert,
    analyze_select,
)
from minidb.contracts.ast import CreateTableStmt, DeleteStmt, InsertStmt, SelectStmt, Statement
from minidb.contracts.bound import (
    BoundCreate,
    BoundDelete,
    BoundInsert,
    BoundSelect,
    BoundStatement,
)
from minidb.contracts.errors import PlanningError, UnsupportedError
from minidb.contracts.plans import (
    CreateTablePlan,
    DeletePlan,
    FilterPlan,
    InsertPlan,
    PlanNode,
    ProjectPlan,
    SeqScanPlan,
)
from minidb.contracts.ports import CatalogPort
from minidb.contracts.results import TraceEvent


def _planning_error(message: str) -> PlanningError:
    return PlanningError("INVALID_BOUND_STATEMENT", message)


def build_plan(bound_stmt: BoundStatement) -> PlanNode:
    """Convert a validated Bound statement into an immutable logical plan."""

    if isinstance(bound_stmt, BoundCreate):
        return CreateTablePlan(bound_stmt.name, bound_stmt.columns, bound_stmt.span)

    if isinstance(bound_stmt, BoundInsert):
        return InsertPlan(bound_stmt.table, bound_stmt.values, bound_stmt.span)

    if isinstance(bound_stmt, BoundSelect):
        scan: PlanNode = SeqScanPlan(bound_stmt.table, bound_stmt.span)
        if bound_stmt.where is not None:
            scan = FilterPlan(scan, bound_stmt.where, bound_stmt.where.span)
        return ProjectPlan(scan, bound_stmt.indices, bound_stmt.names, bound_stmt.span)

    if isinstance(bound_stmt, BoundDelete):
        scan: PlanNode = SeqScanPlan(bound_stmt.table, bound_stmt.span)
        if bound_stmt.where is not None:
            scan = FilterPlan(scan, bound_stmt.where, bound_stmt.where.span)
        return DeletePlan(bound_stmt.table, scan, bound_stmt.span)

    raise _planning_error(f"不支持的 Bound 语句 {type(bound_stmt).__name__}")


class Compiler:
    """Read-only semantic-to-plan compiler facade."""

    def __init__(self, on_trace: Callable[[TraceEvent], None] | None = None) -> None:
        self._on_trace = on_trace

    def _trace(self, stage: str, detail: str) -> None:
        if self._on_trace is not None:
            self._on_trace(TraceEvent(stage, detail))

    def compile(self, statement: Statement, catalog: CatalogPort) -> PlanNode:
        """Analyze one AST statement, then build its logical plan."""

        if isinstance(statement, CreateTableStmt):
            bound = analyze_create(statement, catalog)
        elif isinstance(statement, InsertStmt):
            bound = analyze_insert(statement, catalog)
        elif isinstance(statement, SelectStmt):
            bound = analyze_select(statement, catalog)
        elif isinstance(statement, DeleteStmt):
            bound = analyze_delete(statement, catalog)
        else:
            raise UnsupportedError(
                "UNSUPPORTED_STATEMENT",
                f"不支持的语句 {type(statement).__name__}",
            )

        self._trace("SEMANTIC", type(bound).__name__)
        plan = build_plan(bound)
        self._trace("PLAN", type(plan).__name__)
        return plan


__all__ = ["Compiler", "build_plan"]
