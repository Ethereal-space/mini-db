"""C-E04 页 CRC32 校验工具。

v2 页把最后四个字节作为 little-endian CRC32。计算前该区域清零，因而
同一页内容总能得到确定结果；v1 页没有校验区，调用方应继续走原格式。
"""

from __future__ import annotations

import struct
import zlib

from minidb.contracts.errors import StorageError

from .constants import CHECKSUM_OFFSET, CHECKSUM_SIZE, PAGE_SIZE


class ChecksumError(StorageError):
    """页 CRC 与存储值不一致。"""


def _validate_page(data: bytes | bytearray | memoryview) -> bytes:
    raw = bytes(data)
    if len(raw) != PAGE_SIZE:
        raise ValueError(f"页数据必须恰好为 {PAGE_SIZE} 字节，实际为 {len(raw)}")
    return raw


def crc32(data: bytes | bytearray | memoryview) -> int:
    """计算 v2 页 CRC32；输入的 checksum 区会按零处理。"""

    raw = bytearray(_validate_page(data))
    raw[CHECKSUM_OFFSET : CHECKSUM_OFFSET + CHECKSUM_SIZE] = bytes(CHECKSUM_SIZE)
    return zlib.crc32(raw) & 0xFFFFFFFF


def seal_page(data: bytes | bytearray | memoryview) -> bytes:
    """返回带 CRC 的完整 v2 页，不修改调用方缓冲区。"""

    raw = bytearray(_validate_page(data))
    raw[CHECKSUM_OFFSET : CHECKSUM_OFFSET + CHECKSUM_SIZE] = bytes(CHECKSUM_SIZE)
    struct.pack_into("<I", raw, CHECKSUM_OFFSET, zlib.crc32(raw) & 0xFFFFFFFF)
    return bytes(raw)


def verify_page(
    data: bytes | bytearray | memoryview,
    *,
    page_id: int | None = None,
) -> int:
    """验证页 CRC，成功返回实际 CRC，失败抛带页号上下文的 ChecksumError。"""

    raw = _validate_page(data)
    actual = struct.unpack_from("<I", raw, CHECKSUM_OFFSET)[0]
    expected = crc32(raw)
    if actual != expected:
        context: dict[str, object] = {
            "expected_crc": expected,
            "actual_crc": actual,
            "checksum_offset": CHECKSUM_OFFSET,
        }
        if page_id is not None:
            context["page_id"] = page_id
        raise ChecksumError(
            "CHECKSUM_MISMATCH",
            f"page {page_id if page_id is not None else '?'} CRC 校验失败：期望 {expected:#010x}，实际 {actual:#010x}",
            span=None,
            context=context,
        )
    return actual


compute_crc32 = crc32
add_checksum = seal_page
check_checksum = verify_page
crc_page = crc32
seal = seal_page
verify_crc = verify_page
ChecksumMismatchError = ChecksumError


__all__ = [
    "ChecksumError",
    "ChecksumMismatchError",
    "add_checksum",
    "check_checksum",
    "compute_crc32",
    "crc32",
    "crc_page",
    "seal",
    "seal_page",
    "verify_crc",
    "verify_page",
]
