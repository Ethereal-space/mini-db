"""LRU/FIFO 页替换策略；策略本身不执行磁盘 I/O。"""

from __future__ import annotations

from collections import OrderedDict
from enum import Enum


class ReplacementPolicy(str, Enum):
    LRU = "LRU"
    FIFO = "FIFO"
    CLOCK = "CLOCK"


def _policy(value: str | ReplacementPolicy) -> ReplacementPolicy:
    if isinstance(value, ReplacementPolicy):
        return value
    try:
        return ReplacementPolicy(str(value).upper())
    except ValueError as error:
        raise ValueError("替换策略必须是 LRU、FIFO 或 CLOCK") from error


class Replacer:
    """维护已装入页的顺序和 pin 计数。"""

    def __init__(self, capacity: int, policy: str | ReplacementPolicy = ReplacementPolicy.LRU) -> None:
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
            raise ValueError("替换器 capacity 必须是正整数")
        self.capacity = capacity
        self.policy = _policy(policy)
        self._order: OrderedDict[int, None] = OrderedDict()
        self._pins: dict[int, int] = {}
        self._reference: dict[int, bool] = {}
        self._hand_index = 0
        self._last_scan_steps = 0

    def record_load(self, page_id: int) -> None:
        if page_id in self._order:
            self.record_access(page_id)
            return
        if len(self._order) >= self.capacity:
            raise ValueError("替换器已达到容量，必须先移除牺牲页")
        self._order[page_id] = None
        self._pins[page_id] = 0
        self._reference[page_id] = self.policy is ReplacementPolicy.CLOCK

    def record_access(self, page_id: int) -> None:
        self._require(page_id)
        if self.policy is ReplacementPolicy.LRU:
            self._order.move_to_end(page_id)
        elif self.policy is ReplacementPolicy.CLOCK:
            self._reference[page_id] = True

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
        if self.policy is ReplacementPolicy.CLOCK:
            return self._choose_clock_victim()
        for page_id in self._order:
            if self._pins[page_id] == 0:
                return page_id
        return None

    def remove(self, page_id: int) -> None:
        self._require(page_id)
        if self._pins[page_id] != 0:
            raise ValueError(f"page {page_id} 仍被 pin，不能从替换器移除")
        ids = tuple(self._order)
        removed_index = ids.index(page_id)
        del self._order[page_id]
        del self._pins[page_id]
        self._reference.pop(page_id, None)
        if self._order:
            if self.policy is ReplacementPolicy.CLOCK:
                if removed_index < self._hand_index:
                    self._hand_index -= 1
                self._hand_index %= len(self._order)
        else:
            self._hand_index = 0

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

    @property
    def reference_bits(self) -> dict[int, bool]:
        """返回 Clock 引用位快照；LRU/FIFO 返回空字典。"""

        return dict(self._reference) if self.policy is ReplacementPolicy.CLOCK else {}

    @property
    def hand(self) -> int | None:
        if not self._order or self.policy is not ReplacementPolicy.CLOCK:
            return None
        return tuple(self._order)[self._hand_index % len(self._order)]

    @property
    def hand_index(self) -> int:
        return self._hand_index

    @property
    def scan_steps(self) -> int:
        return self._last_scan_steps

    def reference_bit(self, page_id: int) -> bool:
        self._require(page_id)
        return self._reference.get(page_id, False)

    def set_reference(self, page_id: int, value: bool) -> None:
        self._require(page_id)
        if self.policy is not ReplacementPolicy.CLOCK:
            raise ValueError("只有 Clock 替换器支持 reference bit")
        self._reference[page_id] = bool(value)

    def _choose_clock_victim(self) -> int | None:
        if not self._order:
            self._last_scan_steps = 0
            return None
        # 两轮足以把所有未 pin 页的引用位清零并再次观察；全 pin 时
        # 也会在有限步数内返回 None，不会因环形扫描而死循环。
        max_steps = max(1, 2 * len(self._order))
        steps = 0
        while steps < max_steps and self._order:
            ids = tuple(self._order)
            self._hand_index %= len(ids)
            page_id = ids[self._hand_index]
            self._hand_index = (self._hand_index + 1) % len(ids)
            steps += 1
            if self._pins[page_id] != 0:
                continue
            if self._reference.get(page_id, False):
                self._reference[page_id] = False
                continue
            self._last_scan_steps = steps
            return page_id
        self._last_scan_steps = steps
        return None


class LRUReplacer(Replacer):
    def __init__(self, capacity: int) -> None:
        super().__init__(capacity, ReplacementPolicy.LRU)


class FIFOReplacer(Replacer):
    def __init__(self, capacity: int) -> None:
        super().__init__(capacity, ReplacementPolicy.FIFO)


class ClockReplacer(Replacer):
    def __init__(self, capacity: int) -> None:
        super().__init__(capacity, ReplacementPolicy.CLOCK)


def make_replacer(capacity: int, policy: str | ReplacementPolicy = ReplacementPolicy.LRU) -> Replacer:
    """按策略创建替换器，供缓存装配和独立实验使用。"""

    selected = _policy(policy)
    if selected is ReplacementPolicy.CLOCK:
        return ClockReplacer(capacity)
    if selected is ReplacementPolicy.FIFO:
        return FIFOReplacer(capacity)
    return LRUReplacer(capacity)


__all__ = ["ClockReplacer", "FIFOReplacer", "LRUReplacer", "ReplacementPolicy", "Replacer", "make_replacer"]
