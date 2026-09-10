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
    """缓存所有帧都被 pin，无法装入新页。"""


def _buffer_error(code: str, message: str, **context: object) -> StorageError:
    return StorageError(code, message, span=None, context=context)


@dataclass(slots=True)
class Frame:
    page_id: int
    data: bytearray
    pin_count: int = 0
    dirty: bool = False

    def __post_init__(self) -> None:
        if len(self.data) != PAGE_SIZE:
            raise ValueError(f"Frame 数据必须为 {PAGE_SIZE} 字节")
        if self.pin_count < 0:
            raise ValueError("Frame.pin_count 不能为负数")


class BufferPool:
    """页号到 Frame 的有限映射，磁盘对象通过最小鸭子类型注入。"""

    def __init__(
        self,
        disk: DiskManager | str | Path,
        pool_size: int = 8,
        policy: str | ReplacementPolicy = ReplacementPolicy.LRU,
        *,
        owns_disk: bool | None = None,
    ) -> None:
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
        return self._disk

    @property
    def capacity(self) -> int:
        return self.pool_size

    @property
    def frames(self) -> dict[int, Frame]:
        return dict(self._frames)

    @property
    def events(self) -> tuple[BufferEvent, ...]:
        return tuple(self._events)

    @property
    def pinned_pages(self) -> int:
        return sum(frame.pin_count > 0 for frame in self._frames.values())

    def pin_count(self, page_id: int) -> int:
        frame = self._frames.get(page_id)
        return frame.pin_count if frame is not None else 0

    def is_pinned(self, page_id: int) -> bool:
        return self.pin_count(page_id) > 0

    def get_frame(self, page_id: int) -> Frame | None:
        return self._frames.get(page_id)

    @contextmanager
    def fetch_page(self, page_id: int) -> Iterator[Frame]:
        """获取并 pin 一个 Frame，离开 with 块时无条件解除 pin。"""

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
        page_id = frame_or_page.page_id if isinstance(frame_or_page, Frame) else frame_or_page
        frame = self._frames.get(page_id)
        if frame is None or (isinstance(frame_or_page, Frame) and frame is not frame_or_page):
            raise _buffer_error("PAGE_NOT_CACHED", f"page {page_id} 不在缓存中", page_id=page_id)
        frame.dirty = True

    def flush_page(self, page_id: int) -> None:
        frame = self._frames.get(page_id)
        if frame is None:
            raise _buffer_error("PAGE_NOT_CACHED", f"page {page_id} 不在缓存中", page_id=page_id)
        if frame.dirty:
            self._write_frame(frame)

    def flush_all(self) -> None:
        self._check_open()
        for page_id in tuple(self._frames):
            self.flush_page(page_id)
        flush = getattr(self._disk, "flush", None)
        if callable(flush):
            flush()

    def invalidate(self, page_id: int) -> None:
        """丢弃指定帧；释放页前调用，故意不写回 dirty 数据。"""

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
        return self.replacer.choose_victim()

    def stats(self) -> BufferStats:
        return BufferStats(self._hits, self._misses, self._evictions, self._reads, self._writes)

    get_stats = stats
    page = fetch_page

    def close(self) -> None:
        if self._closed:
            return
        self.flush_all()
        if self._owns_disk:
            self._disk.close()
        self._closed = True

    def __enter__(self) -> "BufferPool":
        self._check_open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _write_frame(self, frame: Frame) -> None:
        self._disk.write_page(frame.page_id, bytes(frame.data))
        self._writes += 1
        frame.dirty = False
        self._events.append(BufferEvent("write", frame.page_id, "dirty=true"))

    def _unpin_frame(self, frame: Frame) -> None:
        if frame.pin_count <= 0:
            raise _buffer_error("PIN_UNDERFLOW", f"page {frame.page_id} pin_count 不能为负", page_id=frame.page_id)
        frame.pin_count -= 1
        self.replacer.unpin(frame.page_id)

    def _check_open(self) -> None:
        if self._closed:
            raise _buffer_error("CLOSED_BUFFER", "BufferPool 已关闭")


BufferFrame = Frame
BufferPoolFullError = BufferFullError


__all__ = ["BufferFrame", "BufferFullError", "BufferPool", "BufferPoolFullError", "Frame"]
