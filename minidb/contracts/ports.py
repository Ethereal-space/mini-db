"""成员 A 对外端口；返回整批语句或抛出错误，无存储副作用。"""

from typing import Protocol, runtime_checkable

from .ast import Statement


@runtime_checkable
class FrontendPort(Protocol):
    def parse(self, source: str) -> list[Statement]: ...
