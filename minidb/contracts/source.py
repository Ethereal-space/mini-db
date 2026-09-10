"""源代码位置契约。

``offset`` 以 Unicode 码点计数并从 0 开始，``line`` 与 ``column`` 从 1 开始。
``Span`` 使用左闭右开区间，便于直接切片原 SQL 文本。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class Position:
    """SQL 文本中的一个位置。"""

    offset: int
    line: int
    column: int

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise ValueError("offset 必须大于或等于 0")
        if self.line < 1:
            raise ValueError("line 必须大于或等于 1")
        if self.column < 1:
            raise ValueError("column 必须大于或等于 1")


@dataclass(frozen=True, slots=True)
class Span:
    """源代码中的左闭右开区间。"""

    start: Position
    end: Position

    def __post_init__(self) -> None:
        if self.end.offset < self.start.offset:
            raise ValueError("Span.end 不能位于 Span.start 之前")

    @property
    def length(self) -> int:
        """以 Unicode 码点表示的区间长度。"""

        return self.end.offset - self.start.offset

    @classmethod
    def at(cls, position: Position) -> Span:
        """创建一个位于 ``position`` 的零宽区间。"""

        return cls(position, position)

    @classmethod
    def covering(cls, first: Span, *rest: Span) -> Span:
        """创建覆盖所有给定区间的最小区间。"""

        spans = (first, *rest)
        start = min((item.start for item in spans), key=lambda item: item.offset)
        end = max((item.end for item in spans), key=lambda item: item.offset)
        return cls(start, end)


UNKNOWN_POSITION = Position(offset=0, line=1, column=1)
UNKNOWN_SPAN = Span.at(UNKNOWN_POSITION)

__all__ = ["Position", "Span", "UNKNOWN_POSITION", "UNKNOWN_SPAN"]
