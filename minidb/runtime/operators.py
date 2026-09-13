"""SeqScan、Filter 和 Project 的拉取式运行时算子。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from minidb.contracts import (
    ExecutionError,
    FilterPlan,
    PlanNode,
    ProjectPlan,
    SeqScanPlan,
    StoragePort,
    StoredRow,
)

from .expression_evaluator import evaluate


class RowOperator(Protocol):
    """运行时内部统一使用的拉取式行算子协议。"""

    output_count: int

    def open(self) -> None: ...

    def next(self) -> StoredRow | None: ...

    def close(self) -> None: ...


def _close_preserving_error(operator: RowOperator, error: Exception) -> None:
    try:
        operator.close()
    except Exception as close_error:
        error.add_note(f"关闭算子时又发生错误：{close_error}")


class SeqScanOperator:
    """从 StoragePort.scan 逐行拉取已经复制的 StoredRow。"""

    def __init__(self, plan: SeqScanPlan, storage: StoragePort) -> None:
        self.plan = plan
        self.storage = storage
        self.output_count = 0
        self._iterator: Iterator[StoredRow] | None = None
        self._opened = False
        self._finished = False
        self._closed = True

    def open(self) -> None:
        if self._opened and not self._closed:
            self.close()
        self.output_count = 0
        self._opened = False
        self._finished = False
        self._closed = False
        try:
            self._iterator = iter(self.storage.scan(self.plan.table))
        except Exception:
            self._closed = True
            self._iterator = None
            raise
        self._opened = True

    def next(self) -> StoredRow | None:
        if not self._opened:
            raise ExecutionError("OPERATOR_NOT_OPEN", "SeqScan 必须先 open", self.plan.span)
        if self._finished:
            return None
        assert self._iterator is not None
        try:
            row = next(self._iterator)
        except StopIteration:
            self.close()
            return None
        except Exception as error:
            _close_preserving_error(self, error)
            raise
        if not isinstance(row, StoredRow):
            error = ExecutionError(
                "INVALID_STORAGE_ROW",
                f"StoragePort.scan 返回了 {type(row).__name__}，预期 StoredRow",
                self.plan.span,
            )
            _close_preserving_error(self, error)
            raise error
        self.output_count += 1
        return row

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._finished = True
        iterator = self._iterator
        self._iterator = None
        close = getattr(iterator, "close", None)
        if callable(close):
            close()


class FilterOperator:
    """循环跳过不匹配行，保持固定调用栈深度。"""

    def __init__(self, plan: FilterPlan, child: RowOperator) -> None:
        self.plan = plan
        self.child = child
        self.output_count = 0
        self._opened = False
        self._finished = False
        self._closed = True

    def open(self) -> None:
        if self._opened and not self._closed:
            self.close()
        self.output_count = 0
        self._opened = False
        self._finished = False
        self._closed = False
        try:
            self.child.open()
        except Exception as error:
            _close_preserving_error(self.child, error)
            self._closed = True
            self._finished = True
            raise
        self._opened = True

    def next(self) -> StoredRow | None:
        if not self._opened:
            raise ExecutionError("OPERATOR_NOT_OPEN", "Filter 必须先 open", self.plan.span)
        if self._finished:
            return None
        try:
            while True:
                row = self.child.next()
                if row is None:
                    self.close()
                    return None
                matched = evaluate(self.plan.predicate, row.values)
                if not isinstance(matched, bool):
                    raise ExecutionError(
                        "PREDICATE_NOT_BOOLEAN",
                        "Filter 谓词必须产生 BOOL",
                        self.plan.predicate.span,
                        {"actual_type": type(matched).__name__},
                    )
                if matched:
                    self.output_count += 1
                    return row
        except Exception as error:
            _close_preserving_error(self, error)
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._finished = True
        self.child.close()


class ProjectOperator:
    """按稳定下标创建新的值 tuple，同时原样保留物理 RID。"""

    def __init__(self, plan: ProjectPlan, child: RowOperator) -> None:
        self.plan = plan
        self.child = child
        self.output_count = 0
        self._opened = False
        self._finished = False
        self._closed = True

    def open(self) -> None:
        if self._opened and not self._closed:
            self.close()
        self.output_count = 0
        self._opened = False
        self._finished = False
        self._closed = False
        try:
            self.child.open()
        except Exception as error:
            _close_preserving_error(self.child, error)
            self._closed = True
            self._finished = True
            raise
        self._opened = True

    def next(self) -> StoredRow | None:
        if not self._opened:
            raise ExecutionError("OPERATOR_NOT_OPEN", "Project 必须先 open", self.plan.span)
        if self._finished:
            return None
        try:
            row = self.child.next()
            if row is None:
                self.close()
                return None
            for index in self.plan.indices:
                if index >= len(row.values):
                    raise ExecutionError(
                        "PROJECT_INDEX_OUT_OF_RANGE",
                        f"投影下标 {index} 超出行宽度 {len(row.values)}",
                        self.plan.span,
                        {"index": index, "row_width": len(row.values)},
                    )
            projected = StoredRow(row.rid, tuple(row.values[index] for index in self.plan.indices))
            self.output_count += 1
            return projected
        except Exception as error:
            _close_preserving_error(self, error)
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._finished = True
        self.child.close()


def build_operator(plan: PlanNode, storage: StoragePort) -> RowOperator:
    """从核心查询 Plan 递归构造算子树。"""

    if isinstance(plan, SeqScanPlan):
        return SeqScanOperator(plan, storage)
    if isinstance(plan, FilterPlan):
        return FilterOperator(plan, build_operator(plan.child, storage))
    if isinstance(plan, ProjectPlan):
        return ProjectOperator(plan, build_operator(plan.child, storage))
    raise ExecutionError(
        "INVALID_OPERATOR_PLAN",
        f"计划 {type(plan).__name__} 不能构造成查询算子",
        getattr(plan, "span", None),
        {"plan_type": type(plan).__name__},
    )


__all__ = [
    "FilterOperator",
    "ProjectOperator",
    "RowOperator",
    "SeqScanOperator",
    "build_operator",
]
