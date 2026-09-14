"""手册规定的扩展语句容器。payload 深复制后按只读约定使用。"""

from collections.abc import Mapping
from dataclasses import dataclass
import math

from .source import Span


def _copy_json(payload: Mapping) -> dict:
    """只接收 JSON 值；显式栈深复制，拒绝循环和 Python AST 对象。"""
    if not isinstance(payload, Mapping):
        raise TypeError("扩展 payload 必须为 Mapping")
    root = [None]
    pending = [(payload, root, 0)]
    ancestors = set()
    while pending:
        item = pending.pop()
        if isinstance(item, int):
            ancestors.remove(item)
            continue
        value, parent, key = item
        if isinstance(value, Mapping) or type(value) is list:
            identity = id(value)
            if identity in ancestors:
                raise ValueError("扩展 payload 不能包含循环引用")
            ancestors.add(identity)
            pending.append(identity)
            if isinstance(value, Mapping):
                target = {}
                entries = list(value.items())
                if any(type(name) is not str for name, _ in entries):
                    raise TypeError("JSON 对象的键必须为字符串")
                for name, _ in entries:
                    target[name] = None
            else:
                target = [None] * len(value)
                entries = list(enumerate(value))
            parent[key] = target
            for name, child in reversed(entries):
                pending.append((child, target, name))
        elif value is None or type(value) in (str, int, bool):
            parent[key] = value
        elif type(value) is float and math.isfinite(value):
            parent[key] = value
        else:
            raise TypeError(f"扩展 payload 只允许 JSON 值，收到 {type(value).__name__}")
    return root[0]


@dataclass(frozen=True)
class ExtensionStatement:
    feature: str
    version: int
    payload: Mapping
    span: Span

    def __post_init__(self) -> None:
        if type(self.feature) is not str or not self.feature:
            raise ValueError("feature 必须为非空字符串")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("version 必须为正整数")
        object.__setattr__(self, "payload", _copy_json(self.payload))
