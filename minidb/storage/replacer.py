"""LRU/FIFO 页替换策略；策略本身不执行磁盘 I/O。"""

from __future__ import annotations

from collections import OrderedDict
from enum import Enum


class ReplacementPolicy(str, Enum):
    LRU = "LRU"
    FIFO = "FIFO"


def _policy(value: str | ReplacementPolicy) -> ReplacementPolicy:
    if isinstance(value, ReplacementPolicy):
        return value
    try:
        return ReplacementPolicy(str(value).upper())
    except ValueError as error:
        raise ValueError("替换策略必须是 LRU 或 FIFO") from error


class Replacer:
    """维护已装入页的顺序和 pin 计数。"""

    def __init__(self, capacity: int, policy: str | ReplacementPolicy = ReplacementPolicy.LRU) -> None:
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
            raise ValueError("替换器 capacity 必须是正整数")
        self.capacity = capacity
        self.policy = _policy(policy)
        self._order: OrderedDict[int, None] = OrderedDict()
        self._pins: dict[int, int] = {}

    def record_load(self, page_id: int) -> None:
        if page_id in self._order:
            self.record_access(page_id)
            return
        if len(self._order) >= self.capacity:
            raise ValueError("替换器已达到容量，必须先移除牺牲页")
        self._order[page_id] = None
        self._pins[page_id] = 0

    def record_access(self, page_id: int) -> None:
        self._require(page_id)
        if self.policy is ReplacementPolicy.LRU:
            self._order.move_to_end(page_id)

    def pin(self, page_id: int) -> None:
        self._require(page_id)
        self._pins[page_id] += 1

    def unpin(self, page_id: int) -> None:
        self._require(page_id)
        count = self._pins[page_id]
        if count <= 0:
            raise ValueError(f"page {page_id} 的 pin_count 已经为零")
        self._pins[page_id] = count - 1

    def set_evictable(self, page_id: int, evictable: bool) -> None:
        self._require(page_id)
        if evictable:
            self._pins[page_id] = 0
        elif self._pins[page_id] == 0:
            self._pins[page_id] = 1

    def pin_count(self, page_id: int) -> int:
        self._require(page_id)
        return self._pins[page_id]

    def is_pinned(self, page_id: int) -> bool:
        return self.pin_count(page_id) > 0

    def choose_victim(self) -> int | None:
        for page_id in self._order:
            if self._pins[page_id] == 0:
                return page_id
        return None

    def remove(self, page_id: int) -> None:
        self._require(page_id)
        if self._pins[page_id] != 0:
            raise ValueError(f"page {page_id} 仍被 pin，不能从替换器移除")
        del self._order[page_id]
        del self._pins[page_id]

    @property
    def evictable_pages(self) -> tuple[int, ...]:
        return tuple(page_id for page_id in self._order if self._pins[page_id] == 0)

    @property
    def page_ids(self) -> tuple[int, ...]:
        return tuple(self._order)

    def __contains__(self, page_id: object) -> bool:
        return page_id in self._order

    def __len__(self) -> int:
        return len(self._order)

    def _require(self, page_id: int) -> None:
        if page_id not in self._order:
            raise KeyError(f"page {page_id} 不在替换器中")


class LRUReplacer(Replacer):
    def __init__(self, capacity: int) -> None:
        super().__init__(capacity, ReplacementPolicy.LRU)


class FIFOReplacer(Replacer):
    def __init__(self, capacity: int) -> None:
        super().__init__(capacity, ReplacementPolicy.FIFO)


__all__ = ["FIFOReplacer", "LRUReplacer", "ReplacementPolicy", "Replacer"]
