"""源码坐标：offset 为 Unicode 码点，行列为一基，Span 为半开区间。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    offset: int
    line: int
    column: int

    def __post_init__(self) -> None:
        if self.offset < 0 or self.line < 1 or self.column < 1:
            raise ValueError("源码坐标必须满足 offset>=0、line>=1、column>=1")


@dataclass(frozen=True)
class Span:
    start: Position
    end: Position

    def __post_init__(self) -> None:
        if self.end.offset < self.start.offset:
            raise ValueError("Span 终点不能先于起点")
