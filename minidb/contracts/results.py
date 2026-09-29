"""执行结果、行定位与缓冲池观测数据契约。"""

from __future__ import annotations

from dataclasses import dataclass

from .metadata import StoredValue


@dataclass(frozen=True, slots=True, order=True)
class RID:
    page_id: int
    slot_id: int

    def __post_init__(self) -> None:
        if self.page_id < 1:
            raise ValueError("RID.page_id 必须大于或等于 1")
        if self.slot_id < 0:
            raise ValueError("RID.slot_id 必须大于或等于 0")


@dataclass(frozen=True, slots=True)
class StoredRow:
    rid: RID
    values: tuple[StoredValue, ...]


@dataclass(frozen=True, slots=True)
class TraceEvent:
    stage: str
    detail: str

    def __post_init__(self) -> None:
        if not self.stage:
            raise ValueError("TraceEvent.stage 不能为空")


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[StoredValue, ...], ...] = ()
    affected_rows: int = 0
    message: str = ""
    trace: tuple[TraceEvent, ...] = ()

    def __post_init__(self) -> None:
        if self.affected_rows < 0:
            raise ValueError("ExecutionResult.affected_rows 不能为负数")
        if self.rows and not self.columns:
            raise ValueError("查询结果含行时必须提供 columns")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("ExecutionResult 中每行宽度必须等于 columns 宽度")


@dataclass(frozen=True, slots=True)
class BufferStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    reads: int = 0
    writes: int = 0

    def __post_init__(self) -> None:
        if any(value < 0 for value in (self.hits, self.misses, self.evictions, self.reads, self.writes)):
            raise ValueError("BufferStats 的计数不能为负数")


@dataclass(frozen=True, slots=True)
class BufferEvent:
    action: str
    page_id: int
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.action:
            raise ValueError("BufferEvent.action 不能为空")
        if self.page_id < 0:
            raise ValueError("BufferEvent.page_id 不能为负数")


__all__ = ["BufferEvent", "BufferStats", "ExecutionResult", "RID", "StoredRow", "TraceEvent"]
