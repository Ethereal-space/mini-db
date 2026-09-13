"""核心逻辑计划分派、查询收集和执行阶段 Trace。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from minidb.contracts import (
    BufferStats,
    CatalogPort,
    CreateTablePlan,
    DeletePlan,
    ExecutionResult,
    ExtensionPlan,
    FilterPlan,
    InsertPlan,
    PlanNode,
    ProjectPlan,
    SeqScanPlan,
    StoragePort,
    TraceEvent,
    UnsupportedError,
)

from .command_executors import execute_create, execute_delete, execute_insert
from .operators import build_operator


TraceSink = Callable[[TraceEvent], None]


def _query_columns(plan: SeqScanPlan | FilterPlan | ProjectPlan) -> tuple[str, ...]:
    if isinstance(plan, ProjectPlan):
        return plan.names
    if isinstance(plan, FilterPlan):
        child = plan.child
        if not isinstance(child, (SeqScanPlan, FilterPlan, ProjectPlan)):
            raise TypeError("FilterPlan.child 不是核心查询计划")
        return _query_columns(child)
    return tuple(column.name for column in plan.table.columns)


def _format_stats(stats: BufferStats) -> str:
    return (
        f"BUFFER hits={stats.hits} misses={stats.misses} evictions={stats.evictions} "
        f"reads={stats.reads} writes={stats.writes}"
    )


class ExecutionService:
    """只消费冻结 Plan 的 ExecutorPort 实现。"""

    def __init__(
        self,
        catalog: CatalogPort,
        storage: StoragePort,
        trace_sink: TraceSink | None = None,
    ) -> None:
        self._catalog = catalog
        self._storage = storage
        self._trace_sink = trace_sink

    def _emit(self, events: list[TraceEvent], detail: str) -> None:
        event = TraceEvent("EXECUTION", detail)
        events.append(event)
        if self._trace_sink is not None:
            self._trace_sink(event)

    def _finish_command(self, plan: PlanNode, result: ExecutionResult) -> ExecutionResult:
        events: list[TraceEvent] = []
        self._emit(events, f"DISPATCH {type(plan).__name__}")
        for event in result.trace:
            events.append(event)
            if self._trace_sink is not None:
                self._trace_sink(event)
        self._emit(events, _format_stats(self._storage.stats()))
        return replace(result, trace=tuple(events))

    def _execute_query(
        self,
        plan: SeqScanPlan | FilterPlan | ProjectPlan,
    ) -> ExecutionResult:
        events: list[TraceEvent] = []
        operator = build_operator(plan, self._storage)
        rows: list[tuple[int | str, ...]] = []
        self._emit(events, f"OPEN {type(plan).__name__}")
        try:
            operator.open()
            while (row := operator.next()) is not None:
                rows.append(row.values)
                self._emit(events, f"ROW rid={row.rid.page_id}:{row.rid.slot_id}")
        finally:
            operator.close()
            self._emit(events, f"CLOSE {type(plan).__name__}")
        self._emit(events, f"ROWS count={len(rows)}")
        self._emit(events, _format_stats(self._storage.stats()))
        return ExecutionResult(
            columns=_query_columns(plan),
            rows=tuple(rows),
            affected_rows=0,
            message=f"返回 {len(rows)} 行",
            trace=tuple(events),
        )

    def execute(self, plan: PlanNode) -> ExecutionResult:
        """明确分派六类核心 Plan；未知扩展不返回空成功。"""

        if isinstance(plan, CreateTablePlan):
            return self._finish_command(plan, execute_create(plan, self._catalog, self._storage))
        if isinstance(plan, InsertPlan):
            return self._finish_command(plan, execute_insert(plan, self._storage))
        if isinstance(plan, DeletePlan):
            return self._finish_command(plan, execute_delete(plan, self._storage))
        if isinstance(plan, (SeqScanPlan, FilterPlan, ProjectPlan)):
            return self._execute_query(plan)
        if isinstance(plan, ExtensionPlan):
            raise UnsupportedError(
                "UNSUPPORTED_FEATURE",
                f"运行时未注册扩展 {plan.feature} v{plan.version}",
                plan.span,
                {"feature": plan.feature, "version": plan.version},
            )
        raise UnsupportedError(
            "UNSUPPORTED_PLAN",
            f"运行时不支持计划 {type(plan).__name__}",
            getattr(plan, "span", None),
            {"plan_type": type(plan).__name__},
        )


__all__ = ["ExecutionService", "TraceSink"]
