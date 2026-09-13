"""可重建的内存 Free Space Map。

FreeSpaceMap 只保存页头给出的容量提示，不承担格式校验或持久化职责。
TableHeap 在真正插入前仍会读取并验证目标页；提示过期时刷新该项并回到
普通页链扫描，因此丢失或损坏 Map 不会改变存储正确性。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class FreeSpaceEntry:
    page_id: int
    table_id: int
    capacity: int

    def __post_init__(self) -> None:
        if not isinstance(self.page_id, int) or isinstance(self.page_id, bool) or self.page_id < 1:
            raise ValueError("page_id 必须是大于等于 1 的整数")
        if not isinstance(self.table_id, int) or isinstance(self.table_id, bool) or self.table_id < 0:
            raise ValueError("table_id 必须是非负整数")
        if not isinstance(self.capacity, int) or isinstance(self.capacity, bool) or self.capacity < 0:
            raise ValueError("capacity 必须是非负整数")


class FreeSpaceMap:
    """按表保存 ``page_id -> 可用总字节数`` 的确定性索引。

    ``find(required)`` 的参数包含新 Slot 所需的 8 字节。候选按容量从小
    到大、再按 page_id 排序，优先使用刚好足够的页，减少尾部碎片。
    """

    def __init__(self, initial: Mapping[int, int] | None = None) -> None:
        self._entries: dict[int, FreeSpaceEntry] = {}
        self._candidate_checks = 0
        self._candidate_hits = 0
        if initial is not None:
            for page_id, capacity in initial.items():
                self.update(page_id, capacity)

    def update(
        self,
        page_id: int | object,
        capacity: int | None = None,
        *,
        table_id: int = 0,
    ) -> FreeSpaceEntry:
        if not isinstance(page_id, int) or isinstance(page_id, bool):
            if capacity is not None:
                raise ValueError("Page 对象更新不能同时提供 capacity")
            return self.refresh(page_id, table_id=table_id)
        if capacity is None:
            raise ValueError("按 page_id 更新时必须提供 capacity")
        entry = FreeSpaceEntry(page_id, table_id, capacity)
        self._entries[page_id] = entry
        return entry

    add = update
    register = update
    update_page = update

    def register_page(self, page: object, *, table_id: int | None = None) -> FreeSpaceEntry:
        return self.refresh(page, table_id=table_id)

    def refresh(self, page: object, *, table_id: int | None = None) -> FreeSpaceEntry:
        """从真实 Page 对象更新提示。

        ``free_end - free_start`` 是页能够容纳的新 Slot+payload 的连续上界。
        tombstone 不改变这两个字段，所以基础删除不会虚增 Map 容量；页面
        压缩或删除空间复用完成后再次调用即可得到最新提示。
        """

        page_id = getattr(page, "page_id", None)
        free_start = getattr(page, "free_start", None)
        free_end = getattr(page, "free_end", None)
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in (page_id, free_start, free_end)):
            raise ValueError("refresh 需要带 page_id/free_start/free_end 的页对象")
        capacity = max(0, free_end - free_start)
        actual_table_id = getattr(page, "table_id", 0) if table_id is None else table_id
        return self.update(page_id, capacity, table_id=actual_table_id)

    refresh_page = refresh

    def remove(self, page_id: int) -> None:
        self._entries.pop(page_id, None)

    remove_page = remove

    def clear(self, table_id: int | None = None) -> None:
        if table_id is None:
            self._entries.clear()
        else:
            for page_id, entry in tuple(self._entries.items()):
                if entry.table_id == table_id:
                    del self._entries[page_id]

    def find(
        self,
        required: int,
        *,
        table_id: int | None = None,
        exclude: Iterable[int] = (),
    ) -> tuple[int, ...]:
        """返回容量提示足够的候选页，参数 ``required`` 包含新 Slot。"""

        if not isinstance(required, int) or isinstance(required, bool) or required < 0:
            raise ValueError("required 必须是非负整数")
        excluded = set(exclude)
        candidates = [
            entry
            for entry in self._entries.values()
            if entry.capacity >= required
            and (table_id is None or entry.table_id in (0, table_id))
            and entry.page_id not in excluded
        ]
        candidates.sort(key=lambda entry: (entry.capacity, entry.page_id))
        self._candidate_checks += len(self._entries)
        self._candidate_hits += len(candidates)
        return tuple(entry.page_id for entry in candidates)

    candidates = find
    find_candidates = find
    get_candidates = find

    def choose(self, required: int, *, table_id: int | None = None, exclude: Iterable[int] = ()) -> int | None:
        found = self.find(required, table_id=table_id, exclude=exclude)
        return found[0] if found else None

    choose_page = choose

    def get(self, page_id: int) -> FreeSpaceEntry | None:
        return self._entries.get(page_id)

    def capacity(self, page_id: int) -> int | None:
        entry = self.get(page_id)
        return entry.capacity if entry is not None else None

    def snapshot(self, *, table_id: int | None = None) -> tuple[FreeSpaceEntry, ...]:
        entries = [entry for entry in self._entries.values() if table_id is None or entry.table_id == table_id]
        entries.sort(key=lambda entry: entry.page_id)
        return tuple(entries)

    @property
    def entries(self) -> tuple[FreeSpaceEntry, ...]:
        return self.snapshot()

    @property
    def candidate_checks(self) -> int:
        return self._candidate_checks

    @property
    def candidate_hits(self) -> int:
        return self._candidate_hits

    def reset_stats(self) -> None:
        self._candidate_checks = 0
        self._candidate_hits = 0

    def rebuild(self, pages: Iterable[object], *, table_id: int | None = None) -> int:
        """丢弃旧提示并从给定页对象重建，返回记录数。"""

        if table_id is None:
            self.clear()
        else:
            self.clear(table_id)
        count = 0
        for page in pages:
            self.refresh(page, table_id=table_id)
            count += 1
        return count

    rebuild_from_pages = rebuild

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, page_id: object) -> bool:
        return page_id in self._entries


PageFreeSpaceMap = FreeSpaceMap


__all__ = ["FreeSpaceEntry", "FreeSpaceMap", "PageFreeSpaceMap"]
