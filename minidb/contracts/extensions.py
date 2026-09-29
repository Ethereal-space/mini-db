"""可选功能使用的版本化公共信封。

扩展通过 JSON 兼容的 payload 交换数据，避免为未选择的扩展修改核心 AST、Bound AST
或 Plan 类。完整扩展只有在各层对同一 ``feature``/``version`` 建立共同基线后才可整合。
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import TypeAlias

from .source import Span

JSONPrimitive: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONPrimitive | list["JSONValue"] | dict[str, "JSONValue"]
JSONPayload: TypeAlias = Mapping[str, JSONValue]
FrozenJSONValue: TypeAlias = JSONPrimitive | "FrozenJSONList" | "FrozenJSONDict"
FrozenJSONPayload: TypeAlias = "FrozenJSONDict"


class _ReadOnlyJSON:
    @staticmethod
    def _immutable(*args: object, **kwargs: object) -> None:
        raise TypeError("扩展 payload 是只读快照")


class FrozenJSONList(_ReadOnlyJSON, list[FrozenJSONValue]):
    """仍可被标准 ``json`` 编码器识别的只读列表。"""

    __setitem__ = _ReadOnlyJSON._immutable
    __delitem__ = _ReadOnlyJSON._immutable
    __iadd__ = _ReadOnlyJSON._immutable
    __imul__ = _ReadOnlyJSON._immutable
    append = _ReadOnlyJSON._immutable
    clear = _ReadOnlyJSON._immutable
    extend = _ReadOnlyJSON._immutable
    insert = _ReadOnlyJSON._immutable
    pop = _ReadOnlyJSON._immutable
    remove = _ReadOnlyJSON._immutable
    reverse = _ReadOnlyJSON._immutable
    sort = _ReadOnlyJSON._immutable


class FrozenJSONDict(_ReadOnlyJSON, dict[str, FrozenJSONValue]):
    """仍可被标准 ``json`` 编码器识别的只读对象。"""

    __setitem__ = _ReadOnlyJSON._immutable
    __delitem__ = _ReadOnlyJSON._immutable
    __ior__ = _ReadOnlyJSON._immutable
    clear = _ReadOnlyJSON._immutable
    pop = _ReadOnlyJSON._immutable
    popitem = _ReadOnlyJSON._immutable
    setdefault = _ReadOnlyJSON._immutable
    update = _ReadOnlyJSON._immutable


def validate_json_value(value: object, *, path: str = "payload") -> None:
    """验证扩展数据是否可被标准 JSON 编码器安全表达。"""

    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} 的对象键必须是字符串")
            validate_json_value(item, path=f"{path}.{key}")
        return
    raise TypeError(f"{path} 包含非 JSON 类型 {type(value).__name__}")


def freeze_json_value(value: JSONValue) -> FrozenJSONValue:
    """深复制 JSON 值并返回不可变快照。"""

    validate_json_value(value)
    if isinstance(value, list):
        return FrozenJSONList(freeze_json_value(item) for item in value)
    if isinstance(value, dict):
        return FrozenJSONDict({key: freeze_json_value(item) for key, item in value.items()})
    return value


def thaw_json_value(value: FrozenJSONValue) -> JSONValue:
    """把不可变快照转换为可交给 ``json`` 模块的普通值。"""

    if isinstance(value, Mapping):
        return {key: thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [thaw_json_value(item) for item in value]
    return value


def _validate_envelope(feature: str, version: int, payload: JSONPayload) -> FrozenJSONPayload:
    if not feature or feature != feature.casefold():
        raise ValueError("feature 必须是非空的小写规范名")
    if version < 1:
        raise ValueError("version 必须大于或等于 1")
    copied = {key: value for key, value in payload.items()}
    validate_json_value(copied)
    return FrozenJSONDict({key: freeze_json_value(value) for key, value in copied.items()})


@dataclass(frozen=True, slots=True)
class ExtensionStatement:
    feature: str
    version: int
    payload: JSONPayload
    span: Span

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _validate_envelope(self.feature, self.version, self.payload))


@dataclass(frozen=True, slots=True)
class BoundExtension:
    feature: str
    version: int
    payload: JSONPayload
    span: Span

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _validate_envelope(self.feature, self.version, self.payload))


@dataclass(frozen=True, slots=True)
class ExtensionPlan:
    feature: str
    version: int
    payload: JSONPayload
    span: Span

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _validate_envelope(self.feature, self.version, self.payload))


__all__ = [
    "BoundExtension",
    "ExtensionPlan",
    "ExtensionStatement",
    "FrozenJSONDict",
    "FrozenJSONList",
    "FrozenJSONPayload",
    "FrozenJSONValue",
    "JSONPayload",
    "JSONPrimitive",
    "JSONValue",
    "freeze_json_value",
    "thaw_json_value",
    "validate_json_value",
]
