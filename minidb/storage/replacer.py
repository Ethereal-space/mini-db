"""LRU/FIFO 页替换策略；策略本身不执行磁盘 I/O。"""

from __future__ import annotations

from collections import OrderedDict
from enum import Enum


class ReplacementPolicy(str, Enum):
    """声明缓存可选替换算法；由 BufferPool 和 make_replacer 共同使用。"""

    LRU = "LRU"
    FIFO = "FIFO"
    CLOCK = "CLOCK"


def _policy(value: str | ReplacementPolicy) -> ReplacementPolicy:
    """供构造器和工厂统一策略输入；转大写匹配枚举，非法值给出明确错误。"""

    if isinstance(value, ReplacementPolicy):
        return value
    try:
        return ReplacementPolicy(str(value).upper())
    except ValueError as error:
        raise ValueError("替换策略必须是 LRU、FIFO 或 CLOCK") from error


class Replacer:
    """由 BufferPool 调用，维护已装入页的顺序、pin 计数和 Clock 引用位。"""

    def __init__(self, capacity: int, policy: str | ReplacementPolicy = ReplacementPolicy.LRU) -> None:
        """由 BufferPool 或工厂创建；校验容量后初始化顺序表和策略状态。"""

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
        """供缓存装入页后登记；已有页转为访问，新页追加并初始化状态。"""

        if page_id in self._order:
            self.record_access(page_id)
            return
        if len(self._order) >= self.capacity:
            raise ValueError("替换器已达到容量，必须先移除牺牲页")
        self._order[page_id] = None
        self._pins[page_id] = 0
        self._reference[page_id] = self.policy is ReplacementPolicy.CLOCK

    def record_access(self, page_id: int) -> None:
        """供缓存命中路径记录访问；LRU 移到队尾，Clock 设置引用位。"""

        self._require(page_id)
        if self.policy is ReplacementPolicy.LRU:
            self._order.move_to_end(page_id)
        elif self.policy is ReplacementPolicy.CLOCK:
            self._reference[page_id] = True

    def pin(self, page_id: int) -> None:
        """供 fetch_page 固定页；确认页已登记后递增 pin 计数。"""

        self._require(page_id)
        self._pins[page_id] += 1

    def unpin(self, page_id: int) -> None:
        """供 unpin_page 释放页；检查计数非零后递减，避免重复释放。"""

        self._require(page_id)
        count = self._pins[page_id]
        if count <= 0:
            raise ValueError(f"page {page_id} 的 pin_count 已经为零")
        self._pins[page_id] = count - 1

    def set_evictable(self, page_id: int, evictable: bool) -> None:
        """供兼容接口切换可淘汰状态；通过归零或建立最小 pin 计数实现。"""

        self._require(page_id)
        if evictable:
            self._pins[page_id] = 0
        elif self._pins[page_id] == 0:
            self._pins[page_id] = 1

    def pin_count(self, page_id: int) -> int:
        """供 BufferPool 和测试读取固定次数；验证页存在后返回计数。"""

        self._require(page_id)
        return self._pins[page_id]

    def is_pinned(self, page_id: int) -> bool:
        """供淘汰前判断页是否占用；比较 pin_count 是否大于零。"""

        return self.pin_count(page_id) > 0

    def choose_victim(self) -> int | None:
        """供缓存满时选择牺牲页；Clock 环扫，其余策略取首个未固定页。"""

        if self.policy is ReplacementPolicy.CLOCK:
            return self._choose_clock_victim()
        for page_id in self._order:
            if self._pins[page_id] == 0:
                return page_id
        return None

    def remove(self, page_id: int) -> None:
        """供淘汰完成后移除元数据；拒绝固定页并维护 Clock 指针位置。"""

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
        """供诊断获取可淘汰页快照；按当前策略顺序过滤 pin 为零的页。"""

        return tuple(page_id for page_id in self._order if self._pins[page_id] == 0)

    @property
    def page_ids(self) -> tuple[int, ...]:
        """供诊断查看页顺序；把内部有序字典键转换为不可变元组。"""

        return tuple(self._order)

    def __contains__(self, page_id: object) -> bool:
        """支持 ``page_id in replacer``；检查页是否已由缓存登记。"""

        return page_id in self._order

    def __len__(self) -> int:
        """供容量判断和测试读取当前登记页数。"""

        return len(self._order)

    def _require(self, page_id: int) -> None:
        """由所有页操作先调用；页未登记时立即抛出 KeyError。"""

        if page_id not in self._order:
            raise KeyError(f"page {page_id} 不在替换器中")

    @property
    def reference_bits(self) -> dict[int, bool]:
        """供 Clock 诊断返回引用位副本；LRU/FIFO 返回空字典。"""

        return dict(self._reference) if self.policy is ReplacementPolicy.CLOCK else {}

    @property
    def hand(self) -> int | None:
        """供诊断读取 Clock 当前指针；非 Clock 或空集合返回 None。"""

        if not self._order or self.policy is not ReplacementPolicy.CLOCK:
            return None
        return tuple(self._order)[self._hand_index % len(self._order)]

    @property
    def hand_index(self) -> int:
        """供测试读取 Clock 指针索引；直接返回内部位置。"""

        return self._hand_index

    @property
    def scan_steps(self) -> int:
        """供性能诊断读取最近一次 Clock 选页扫描步数。"""

        return self._last_scan_steps

    def reference_bit(self, page_id: int) -> bool:
        """供测试读取单页 Clock 引用位；先确认页已登记。"""

        self._require(page_id)
        return self._reference.get(page_id, False)

    def set_reference(self, page_id: int, value: bool) -> None:
        """供 Clock 测试或恢复路径设置引用位；拒绝其他替换策略。"""

        self._require(page_id)
        if self.policy is not ReplacementPolicy.CLOCK:
            raise ValueError("只有 Clock 替换器支持 reference bit")
        self._reference[page_id] = bool(value)

    def _choose_clock_victim(self) -> int | None:
        """由 choose_victim 调用；环扫两轮、清引用位并返回首个可淘汰页。"""

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
    """LRU 专用构造器；供工厂创建最近最少使用替换器。"""

    def __init__(self, capacity: int) -> None:
        """由 make_replacer 调用；把固定 LRU 策略交给基类初始化。"""

        super().__init__(capacity, ReplacementPolicy.LRU)


class FIFOReplacer(Replacer):
    """FIFO 专用构造器；供工厂创建按装入顺序替换的实例。"""

    def __init__(self, capacity: int) -> None:
        """由 make_replacer 调用；把固定 FIFO 策略交给基类初始化。"""

        super().__init__(capacity, ReplacementPolicy.FIFO)


class ClockReplacer(Replacer):
    """Clock 专用构造器；供工厂创建带引用位和环形指针的实例。"""

    def __init__(self, capacity: int) -> None:
        """由 make_replacer 调用；把固定 CLOCK 策略交给基类初始化。"""

        super().__init__(capacity, ReplacementPolicy.CLOCK)


def make_replacer(capacity: int, policy: str | ReplacementPolicy = ReplacementPolicy.LRU) -> Replacer:
    """供 BufferPool 装配策略；规范化名称并创建对应替换器子类。"""

    selected = _policy(policy)
    if selected is ReplacementPolicy.CLOCK:
        return ClockReplacer(capacity)
    if selected is ReplacementPolicy.FIFO:
        return FIFOReplacer(capacity)
    return LRUReplacer(capacity)


__all__ = ["ClockReplacer", "FIFOReplacer", "LRUReplacer", "ReplacementPolicy", "Replacer", "make_replacer"]
