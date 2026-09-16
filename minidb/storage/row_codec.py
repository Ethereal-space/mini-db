"""MiniDB 核心 INT/VARCHAR 行格式的严格编解码器。"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from typing import Final

from minidb.contracts.errors import StorageError
from minidb.contracts.metadata import ColumnMeta, DataType, TableMeta


MAX_ROW_PAYLOAD: Final[int] = 4096 - 32 - 8
MAX_VARCHAR_BYTES: Final[int] = 255
MAX_VARCHAR_LENGTH: Final[int] = MAX_VARCHAR_BYTES
INT_FORMAT: Final[str] = "<i"
INT_STRUCT = struct.Struct(INT_FORMAT)
VARCHAR_LENGTH_STRUCT = struct.Struct("<H")


class RowEncodeError(StorageError):
    """由 ``RowCodec.encode`` 抛出；表示上层值或 schema 无法转换为固定行格式。"""


class RowTooLargeError(RowEncodeError):
    """由编码长度检查抛出；通知 TableHeap 当前行无法放入单个 Slotted Page。"""


class RowDecodeError(StorageError):
    """由 ``RowCodec.decode`` 抛出；把截断、坏 UTF-8 或尾字节报告给扫描调用方。"""


def _columns(schema: Sequence[ColumnMeta] | TableMeta) -> tuple[ColumnMeta, ...]:
    """供编解码入口统一 schema；TableMeta 取其 columns，序列则冻结为元组。"""

    if isinstance(schema, TableMeta):
        return schema.columns
    return tuple(schema)


def _encode_error(code: str, message: str, **context: object) -> RowEncodeError:
    """供编码分支集中构造无 SQL span 的结构化错误，并保留字段上下文。"""

    return RowEncodeError(code, message, span=None, context=context)


def _too_large(message: str, **context: object) -> RowTooLargeError:
    """供编码末尾调用；把超过单页上限的长度信息包装为专用错误。"""

    return RowTooLargeError("ROW_TOO_LARGE", message, span=None, context=context)


def _decode_error(code: str, message: str, **context: object) -> RowDecodeError:
    """供解码分支集中构造存储格式错误，使 TableHeap 能原样向上传递原因。"""

    return RowDecodeError(code, message, span=None, context=context)


class RowCodec:
    """由 TableHeap 调用的无状态行编解码器；按 schema 顺序处理 INT/VARCHAR，不保存旁路元数据。"""

    @staticmethod
    def encode(schema: Sequence[ColumnMeta] | TableMeta, values: Sequence[object]) -> bytes:
        """由插入路径调用；校验列数/类型，逐列小端编码，拼接后检查单页行大小。"""

        columns = _columns(schema)
        if len(columns) != len(values):
            raise _encode_error(
                "COLUMN_COUNT_MISMATCH",
                f"值数量 {len(values)} 与列数量 {len(columns)} 不一致",
                expected=len(columns),
                actual=len(values),
            )
        encoded_parts: list[bytes] = []
        for index, (column, value) in enumerate(zip(columns, values, strict=True)):
            if not isinstance(column, ColumnMeta):
                raise _encode_error(
                    "INVALID_SCHEMA",
                    f"第 {index} 列不是 ColumnMeta",
                    field_index=index,
                    actual_type=type(column).__name__,
                )
            if column.dtype is DataType.INT:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise _encode_error(
                        "TYPE_MISMATCH",
                        f"列 {column.name} 需要 INT，实际为 {type(value).__name__}",
                        field_index=index,
                        column=column.name,
                        expected="INT",
                        actual_type=type(value).__name__,
                    )
                if not -(1 << 31) <= value <= (1 << 31) - 1:
                    raise _encode_error(
                        "INT_OUT_OF_RANGE",
                        f"列 {column.name} 超出 INT32 范围：{value}",
                        field_index=index,
                        column=column.name,
                        value=value,
                    )
                encoded_parts.append(INT_STRUCT.pack(value))
            elif column.dtype is DataType.VARCHAR:
                if not isinstance(value, str):
                    raise _encode_error(
                        "TYPE_MISMATCH",
                        f"列 {column.name} 需要 VARCHAR，实际为 {type(value).__name__}",
                        field_index=index,
                        column=column.name,
                        expected="VARCHAR",
                        actual_type=type(value).__name__,
                    )
                try:
                    encoded_text = value.encode("utf-8")
                except UnicodeEncodeError as error:
                    raise _encode_error(
                        "INVALID_UTF8",
                        f"列 {column.name} 无法编码为 UTF-8：{error}",
                        field_index=index,
                        column=column.name,
                    ) from error
                if len(encoded_text) > MAX_VARCHAR_BYTES:
                    raise _encode_error(
                        "VARCHAR_TOO_LONG",
                        f"列 {column.name} 的 UTF-8 字节数为 {len(encoded_text)}，上限 {MAX_VARCHAR_BYTES}",
                        field_index=index,
                        column=column.name,
                        byte_length=len(encoded_text),
                    )
                encoded_parts.append(VARCHAR_LENGTH_STRUCT.pack(len(encoded_text)))
                encoded_parts.append(encoded_text)
            else:
                raise _encode_error(
                    "UNSUPPORTED_COLUMN_TYPE",
                    f"列 {column.name} 的持久化类型不受支持：{column.dtype}",
                    field_index=index,
                    column=column.name,
                    dtype=str(column.dtype),
                )
        payload = b"".join(encoded_parts)
        if len(payload) > MAX_ROW_PAYLOAD:
            raise _too_large(
                f"编码行需要 {len(payload)} 字节，单页行上限为 {MAX_ROW_PAYLOAD}",
                payload_length=len(payload),
                max_payload=MAX_ROW_PAYLOAD,
            )
        return payload

    @staticmethod
    def decode(
        schema: Sequence[ColumnMeta] | TableMeta,
        payload: bytes | bytearray | memoryview,
    ) -> tuple[int | str, ...]:
        """由扫描路径调用；按 schema 推进游标、严格解码每列，并拒绝截断和多余尾字节。"""

        columns = _columns(schema)
        raw = bytes(payload)
        if len(raw) > MAX_ROW_PAYLOAD:
            raise _decode_error(
                "ROW_TOO_LARGE",
                f"行字节数 {len(raw)} 超过单页上限 {MAX_ROW_PAYLOAD}",
                payload_length=len(raw),
                max_payload=MAX_ROW_PAYLOAD,
            )
        cursor = 0
        values: list[int | str] = []
        for index, column in enumerate(columns):
            if not isinstance(column, ColumnMeta):
                raise _decode_error(
                    "INVALID_SCHEMA",
                    f"第 {index} 列不是 ColumnMeta",
                    field_index=index,
                    cursor=cursor,
                )
            if column.dtype is DataType.INT:
                if len(raw) - cursor < INT_STRUCT.size:
                    raise _decode_error(
                        "TRUNCATED_INT",
                        f"列 {column.name} 的 INT 需要 {INT_STRUCT.size} 字节",
                        field_index=index,
                        column=column.name,
                        cursor=cursor,
                        remaining=len(raw) - cursor,
                    )
                values.append(INT_STRUCT.unpack_from(raw, cursor)[0])
                cursor += INT_STRUCT.size
            elif column.dtype is DataType.VARCHAR:
                if len(raw) - cursor < VARCHAR_LENGTH_STRUCT.size:
                    raise _decode_error(
                        "TRUNCATED_VARCHAR_LENGTH",
                        f"列 {column.name} 缺少 VARCHAR 长度前缀",
                        field_index=index,
                        column=column.name,
                        cursor=cursor,
                    )
                byte_length = VARCHAR_LENGTH_STRUCT.unpack_from(raw, cursor)[0]
                cursor += VARCHAR_LENGTH_STRUCT.size
                if byte_length > MAX_VARCHAR_BYTES:
                    raise _decode_error(
                        "VARCHAR_TOO_LONG",
                        f"列 {column.name} 的长度前缀为 {byte_length}，上限 {MAX_VARCHAR_BYTES}",
                        field_index=index,
                        column=column.name,
                        cursor=cursor - VARCHAR_LENGTH_STRUCT.size,
                        byte_length=byte_length,
                    )
                if len(raw) - cursor < byte_length:
                    raise _decode_error(
                        "TRUNCATED_VARCHAR",
                        f"列 {column.name} 声明 {byte_length} 字节，剩余 {len(raw) - cursor} 字节",
                        field_index=index,
                        column=column.name,
                        cursor=cursor,
                        expected=byte_length,
                        remaining=len(raw) - cursor,
                    )
                text_bytes = raw[cursor : cursor + byte_length]
                try:
                    values.append(text_bytes.decode("utf-8", errors="strict"))
                except UnicodeDecodeError as error:
                    raise _decode_error(
                        "INVALID_UTF8",
                        f"列 {column.name} 的字节不是合法 UTF-8：{error}",
                        field_index=index,
                        column=column.name,
                        cursor=cursor,
                    ) from error
                cursor += byte_length
            else:
                raise _decode_error(
                    "UNSUPPORTED_COLUMN_TYPE",
                    f"列 {column.name} 的持久化类型不受支持：{column.dtype}",
                    field_index=index,
                    column=column.name,
                    cursor=cursor,
                )
        if cursor != len(raw):
            raise _decode_error(
                "TRAILING_BYTES",
                f"行解码后仍有 {len(raw) - cursor} 个尾字节",
                cursor=cursor,
                payload_length=len(raw),
            )
        return tuple(values)

    @staticmethod
    def encoded_size(schema: Sequence[ColumnMeta] | TableMeta, values: Sequence[object]) -> int:
        """供插入前容量估算调用；复用真实编码流程并返回最终字节数。"""

        return len(RowCodec.encode(schema, values))


encode_row = RowCodec.encode
decode_row = RowCodec.decode
encode = RowCodec.encode
decode = RowCodec.decode
encoded_size = RowCodec.encoded_size


__all__ = [
    "INT_FORMAT",
    "INT_STRUCT",
    "MAX_ROW_PAYLOAD",
    "MAX_VARCHAR_BYTES",
    "MAX_VARCHAR_LENGTH",
    "RowCodec",
    "RowDecodeError",
    "RowEncodeError",
    "RowTooLargeError",
    "VARCHAR_LENGTH_STRUCT",
    "decode_row",
    "decode",
    "encode",
    "encode_row",
    "encoded_size",
]
