from __future__ import annotations

import struct

import pytest

from minidb.contracts.metadata import ColumnMeta, DataType
from minidb.storage.row_codec import (
    MAX_ROW_PAYLOAD,
    RowCodec,
    RowDecodeError,
    RowEncodeError,
    RowTooLargeError,
)


def col(name: str, dtype: DataType, ordinal: int) -> ColumnMeta:
    return ColumnMeta(name, dtype, ordinal)


def test_exact_binary_layout() -> None:
    schema = (col("id", DataType.INT, 0), col("name", DataType.VARCHAR, 1), col("age", DataType.INT, 2))
    values = (1, "Alice", 20)
    payload = RowCodec.encode(schema, values)
    assert payload.hex() == "010000000500416c69636514000000"
    assert len(payload) == 15
    assert RowCodec.decode(schema, payload) == values
    assert RowCodec.encoded_size(schema, values) == len(payload)


def test_int32_boundaries() -> None:
    schema = (col("value", DataType.INT, 0),)
    for value in (-(1 << 31), (1 << 31) - 1):
        assert RowCodec.decode(schema, RowCodec.encode(schema, (value,))) == (value,)
    for value in (-(1 << 31) - 1, 1 << 31, True):
        with pytest.raises(RowEncodeError):
            RowCodec.encode(schema, (value,))


def test_utf8_byte_limit() -> None:
    schema = (col("text", DataType.VARCHAR, 0),)
    assert RowCodec.encode(schema, (b"".decode(),)) == b"\x00\x00"
    eighty_five = "汉" * 85
    encoded = RowCodec.encode(schema, (eighty_five,))
    assert encoded[:2] == struct.pack("<H", 255)
    assert len(encoded) == 257
    assert RowCodec.decode(schema, encoded) == (eighty_five,)
    with pytest.raises(RowEncodeError):
        RowCodec.encode(schema, ("汉" * 86,))


def test_decode_truncation_and_garbage() -> None:
    int_schema = (col("value", DataType.INT, 0),)
    varchar_schema = (col("text", DataType.VARCHAR, 0),)
    invalid_payloads = (
        (int_schema, b"\x01\x00\x00"),
        (varchar_schema, b"\x05\x00abcd"),
        (varchar_schema, b"\x01\x00\xff"),
        (int_schema, b"\x01\x00\x00\x00\x00"),
    )
    for schema, payload in invalid_payloads:
        with pytest.raises(RowDecodeError) as caught:
            RowCodec.decode(schema, payload)
        assert caught.value.context


def test_schema_order_and_types() -> None:
    schema = (col("name", DataType.VARCHAR, 0), col("value", DataType.INT, 1))
    values = ("张三", -2)
    assert RowCodec.decode(schema, RowCodec.encode(schema, values)) == values
    for invalid in (("only one",), ("张三", None), ("张三", 1.5), (1, -2)):
        with pytest.raises(RowEncodeError):
            RowCodec.encode(schema, invalid)


def test_row_size_limit_is_checked_after_all_fields() -> None:
    schema = tuple(col(f"c{i}", DataType.INT, i) for i in range(1015))
    with pytest.raises(RowTooLargeError) as caught:
        RowCodec.encode(schema, tuple(range(1015)))
    assert caught.value.context["payload_length"] == 4060
    assert MAX_ROW_PAYLOAD == 4056
