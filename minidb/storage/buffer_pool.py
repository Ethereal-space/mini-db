"""MiniDB 页缓存：pin 生命周期、dirty 写回和 LRU/FIFO 淘汰。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from minidb.contracts.errors import StorageError
from minidb.contracts.results import BufferEvent, BufferStats

from .constants import PAGE_SIZE
from .disk_manager import DiskManager
from .replacer import ReplacementPolicy, Replacer


class BufferFullError(StorageError):
    """由 fetch_page 在所有帧均被 pin 时抛出，提示调用方先释放页面。"""


def _buffer_error(code: str, message: str, **context: object) -> StorageError:
    """供缓存各错误分支创建统一 StorageError；补齐无 SQL span 的上下文。"""

    return StorageError(code, message, span=None, context=context)


@dataclass(slots=True)
class Frame:
    """表示一个内存页帧；由 BufferPool 装入并交给 with 调用方读写。"""

    page_id: int
    data: bytearray
    pin_count: int = 0
    dirty: bool = False

    def __post_init__(self) -> None:
        """由 dataclass 构造后自动调用；检查页长和 pin 计数基本不变量。"""

        if len(self.data) != PAGE_SIZE:
            raise ValueError(f"Frame 数据必须为 {PAGE_SIZE} 字节")
        if self.pin_count < 0:
            raise ValueError("Frame.pin_count 不能为负数")


class BufferPool:
    """连接 TableHeap 与 DiskManager，缓存有限数量的页并协调替换与写回。"""

    def __init__(
        self,
        disk: DiskManager | str | Path,
        pool_size: int = 8,
        policy: str | ReplacementPolicy = ReplacementPolicy.LRU,
        *,
        owns_disk: bool | None = None,
    ) -> None:
        """由存储装配层创建；打开或接收磁盘、建立替换器并注册协调关系。"""

        if not isinstance(pool_size, int) or isinstance(pool_size, bool) or pool_size <= 0:
            raise ValueError("pool_size 必须是正整数")
        if isinstance(disk, (str, Path)):
            self._disk = DiskManager.open(disk)
            self._owns_disk = True if owns_disk is None else bool(owns_disk)
        else:
            self._disk = disk
            self._owns_disk = bool(owns_disk) if owns_disk is not None else False
        self.pool_size = pool_size
        self.policy = policy.value if isinstance(policy, ReplacementPolicy) else str(policy).upper()
        self.replacer = Replacer(pool_size, self.policy)
        self._frames: dict[int, Frame] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._reads = 0
        self._writes = 0
        self._events: list[BufferEvent] = []
        self._closed = False
        attach = getattr(self._disk, "attach_cache_coordinator", None)
        if callable(attach):
            attach(self)

    @property
    def disk(self):
        """供 TableHeap 和诊断读取底层磁盘对象；不转移其所有权。"""

        return self._disk

    @property
    def capacity(self) -> int:
        """供调用方查询最大帧数；返回初始化时固定的 pool_size。"""

        return self.pool_size

    @property
    def frames(self) -> dict[int, Frame]:
        """供测试和诊断获取帧映射浅副本；避免直接增删内部字典。"""

        return dict(self._frames)

    @property
    def events(self) -> tuple[BufferEvent, ...]:
        """供 Trace 展示缓存行为；按发生顺序返回事件不可变快照。"""

        return tuple(self._events)

    @property
    def pinned_pages(self) -> int:
        """供关闭或释放前检查占用；统计 pin_count 大于零的帧数。"""

        return sum(frame.pin_count > 0 for frame in self._frames.values())

    def pin_count(self, page_id: int) -> int:
        """供 TableHeap 和测试读取指定页固定次数；未缓存页按零处理。"""

        frame = self._frames.get(page_id)
        return frame.pin_count if frame is not None else 0

    def is_pinned(self, page_id: int) -> bool:
        """供释放页前判断是否占用；比较 pin_count 是否大于零。"""

        return self.pin_count(page_id) > 0

    def get_frame(self, page_id: int) -> Frame | None:
        """供诊断读取现有帧引用；只查缓存，不触发磁盘读取或 pin。"""

        return self._frames.get(page_id)

    @contextmanager
    def fetch_page(self, page_id: int) -> Iterator[Frame]:
        """供 TableHeap 以 with 获取页；命中直接 pin，未命中则淘汰、读盘并最终 unpin。"""

        self._check_open()
        frame = self._frames.get(page_id)
        if frame is not None:
            self._hits += 1
            self.replacer.record_access(page_id)
            self.replacer.pin(page_id)
            frame.pin_count += 1
            self._events.append(BufferEvent("hit", page_id, f"policy={self.policy}"))
            try:
                yield frame
            finally:
                self._unpin_frame(frame)
            return

        self._misses += 1
        victim = None
        if len(self._frames) >= self.pool_size:
            victim = self.replacer.choose_victim()
            if victim is None:
                raise BufferFullError(
                    "BUFFER_FULL",
                    "所有缓存帧都被 pin，无法装入新页",
                    span=None,
                    context={"page_id": page_id, "pool_size": self.pool_size},
                )
        raw = bytes(self._disk.read_page(page_id))
        if len(raw) != PAGE_SIZE:
            raise _buffer_error(
                "BAD_DISK_PAGE",
                f"磁盘返回 page {page_id} 的长度为 {len(raw)}，期望 {PAGE_SIZE}",
                page_id=page_id,
            )
        self._reads += 1
        if victim is not None:
            victim_frame = self._frames[victim]
            writeback = victim_frame.dirty
            if writeback:
                self._write_frame(victim_frame)
            self.replacer.remove(victim)
            del self._frames[victim]
            self._evictions += 1
            self._events.append(
                BufferEvent(
                    "evict",
                    victim,
                    f"policy={self.policy};writeback={str(writeback).lower()}",
                )
            )
        frame = Frame(page_id, bytearray(raw), pin_count=1, dirty=False)
        self._frames[page_id] = frame
        self.replacer.record_load(page_id)
        self.replacer.pin(page_id)
        self._events.append(BufferEvent("read", page_id, f"policy={self.policy}"))
        try:
            yield frame
        finally:
            self._unpin_frame(frame)

    get_page = fetch_page

    def unpin_page(self, page_id: int | Frame, *, dirty: bool = False) -> None:
        """供非 with 调用方解除 pin；可先标脏，再同步 Frame 与 Replacer 计数。"""

        if isinstance(page_id, Frame):
            page_id = page_id.page_id
        frame = self._frames.get(page_id)
        if frame is None:
            raise _buffer_error("PAGE_NOT_CACHED", f"page {page_id} 不在缓存中", page_id=page_id)
        if frame.pin_count <= 0:
            raise _buffer_error("PAGE_NOT_PINNED", f"page {page_id} 没有可解除的 pin", page_id=page_id)
        if dirty:
            frame.dirty = True
        self._unpin_frame(frame)

    def mark_dirty(self, frame_or_page: Frame | int) -> None:
        """供页修改路径登记延迟写回；确认对象仍属于缓存后设置 dirty。"""

        page_id = frame_or_page.page_id if isinstance(frame_or_page, Frame) else frame_or_page
        frame = self._frames.get(page_id)
        if frame is None or (isinstance(frame_or_page, Frame) and frame is not frame_or_page):
            raise _buffer_error("PAGE_NOT_CACHED", f"page {page_id} 不在缓存中", page_id=page_id)
        frame.dirty = True

    def flush_page(self, page_id: int) -> None:
        """供显式持久化单页；存在且为脏时调用 _write_frame 写入磁盘。"""

        frame = self._frames.get(page_id)
        if frame is None:
            raise _buffer_error("PAGE_NOT_CACHED", f"page {page_id} 不在缓存中", page_id=page_id)
        if frame.dirty:
            self._write_frame(frame)

    def flush_all(self) -> None:
        """供提交和关闭流程持久化缓存；逐帧 flush 后刷新底层文件句柄。"""

        self._check_open()
        for page_id in tuple(self._frames):
            self.flush_page(page_id)
        flush = getattr(self._disk, "flush", None)
        if callable(flush):
            flush()

    def invalidate(self, page_id: int) -> None:
        """供 DiskManager 释放页前丢弃帧；拒绝 pinned 页并故意不写回脏数据。"""

        frame = self._frames.get(page_id)
        if frame is None:
            return
        if frame.pin_count:
            raise _buffer_error(
                "PAGE_PINNED",
                f"page {page_id} 仍被 pin，不能失效",
                page_id=page_id,
                pin_count=frame.pin_count,
            )
        self.replacer.remove(page_id)
        del self._frames[page_id]
        self._events.append(BufferEvent("invalidate", page_id, "dirty_discarded=true"))

    invalidate_page = invalidate

    def choose_victim(self) -> int | None:
        """供演示和诊断预览牺牲页；把选择委托给当前 Replacer。"""

        return self.replacer.choose_victim()

    def stats(self) -> BufferStats:
        """供 StoragePort 和 Trace 获取累计指标；从计数器构造不可变结果。"""

        return BufferStats(self._hits, self._misses, self._evictions, self._reads, self._writes)

    get_stats = stats
    page = fetch_page

    def close(self) -> None:
        """供上下文退出或显式关闭；幂等刷新全部页，并按所有权关闭磁盘。"""

        if self._closed:
            return
        self.flush_all()
        if self._owns_disk:
            self._disk.close()
        self._closed = True

    def __enter__(self) -> "BufferPool":
        """支持 ``with BufferPool``；检查未关闭后返回自身。"""

        self._check_open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """由上下文管理器调用；无论块内是否异常都执行 close。"""

        self.close()

    def _write_frame(self, frame: Frame) -> None:
        """由淘汰和 flush 调用；整页写盘、累计写次数并清除 dirty。"""

        self._disk.write_page(frame.page_id, bytes(frame.data))
        self._writes += 1
        frame.dirty = False
        self._events.append(BufferEvent("write", frame.page_id, "dirty=true"))

    def _unpin_frame(self, frame: Frame) -> None:
        """由 fetch_page finally 和 unpin_page 调用；防下溢后同步两处计数。"""

        if frame.pin_count <= 0:
            raise _buffer_error("PIN_UNDERFLOW", f"page {frame.page_id} pin_count 不能为负", page_id=frame.page_id)
        frame.pin_count -= 1
        self.replacer.unpin(frame.page_id)

    def _check_open(self) -> None:
        """由需要磁盘工作的公开方法调用；已关闭时抛出明确存储错误。"""

        if self._closed:
            raise _buffer_error("CLOSED_BUFFER", "BufferPool 已关闭")


BufferFrame = Frame
BufferPoolFullError = BufferFullError


__all__ = ["BufferFrame", "BufferFullError", "BufferPool", "BufferPoolFullError", "Frame"]
