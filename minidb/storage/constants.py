"""成员 C 的页尺寸、格式版本和物理布局常量。

这些常量是存储层自己的实现约束，跨成员代码只通过
``minidb.contracts`` 交换数据。格式字节序固定为 little-endian，避免
不同平台打开同一个 ``mini.db`` 时出现布局差异。
"""

from __future__ import annotations

import struct
from typing import Final


# 数据库文件的基础页参数。
PAGE_SIZE: Final[int] = 4096
SUPERBLOCK_PAGE_ID: Final[int] = 0
CATALOG_PAGE_ID: Final[int] = 1
INITIAL_PAGE_COUNT: Final[int] = 2
INVALID_PAGE_ID: Final[int] = -1

# Superblock：magic、版本、页大小、页数、空闲链头、Catalog 链头、下一个表号。
SUPERBLOCK_MAGIC: Final[bytes] = b"MINIDB01"
FORMAT_VERSION: Final[int] = 1
SUPERBLOCK_FORMAT: Final[str] = "<8sIIIiiI"
SUPERBLOCK_STRUCT: Final[struct.Struct] = struct.Struct(SUPERBLOCK_FORMAT)
SUPERBLOCK_SIZE: Final[int] = SUPERBLOCK_STRUCT.size

# 数据页固定 32 字节页头。Catalog 页和普通表页共用该头部布局。
DATA_PAGE_MAGIC: Final[bytes] = b"MDPG"
DATA_PAGE_FORMAT: Final[str] = "<4sIiHHHHIII"
DATA_PAGE_STRUCT: Final[struct.Struct] = struct.Struct(DATA_PAGE_FORMAT)
DATA_PAGE_HEADER_SIZE: Final[int] = DATA_PAGE_STRUCT.size
DATA_PAGE_VERSION: Final[int] = 1

# 释放页沿用数据页头的整数布局，只替换 magic；其余字段必须清零。
FREE_PAGE_MAGIC: Final[bytes] = b"FREE"
FREE_PAGE_FORMAT: Final[str] = DATA_PAGE_FORMAT
FREE_PAGE_STRUCT: Final[struct.Struct] = DATA_PAGE_STRUCT

# 便于页管理代码按通用名称引用同一布局；不产生第二种格式。
PAGE_HEADER_FORMAT: Final[str] = DATA_PAGE_FORMAT
PAGE_HEADER_STRUCT: Final[struct.Struct] = DATA_PAGE_STRUCT
PAGE_HEADER_SIZE: Final[int] = DATA_PAGE_HEADER_SIZE

# 头部中的初始值与无符号字段的边界。
INITIAL_FREE_HEAD: Final[int] = INVALID_PAGE_ID
INITIAL_CATALOG_HEAD: Final[int] = CATALOG_PAGE_ID
INITIAL_NEXT_TABLE_ID: Final[int] = 1
MAX_U32: Final[int] = (1 << 32) - 1
MAX_I32: Final[int] = (1 << 31) - 1

if SUPERBLOCK_SIZE != 32:  # pragma: no cover - 防止格式常量被误改
    raise RuntimeError(f"Superblock 格式必须是 32 字节，实际为 {SUPERBLOCK_SIZE}")
if DATA_PAGE_HEADER_SIZE != 32:  # pragma: no cover - 防止格式常量被误改
    raise RuntimeError(f"数据页头必须是 32 字节，实际为 {DATA_PAGE_HEADER_SIZE}")


__all__ = [
    "CATALOG_PAGE_ID",
    "DATA_PAGE_FORMAT",
    "DATA_PAGE_HEADER_SIZE",
    "DATA_PAGE_MAGIC",
    "DATA_PAGE_STRUCT",
    "DATA_PAGE_VERSION",
    "FORMAT_VERSION",
    "FREE_PAGE_FORMAT",
    "FREE_PAGE_MAGIC",
    "FREE_PAGE_STRUCT",
    "INITIAL_CATALOG_HEAD",
    "INITIAL_FREE_HEAD",
    "INITIAL_NEXT_TABLE_ID",
    "INITIAL_PAGE_COUNT",
    "INVALID_PAGE_ID",
    "MAX_I32",
    "MAX_U32",
    "PAGE_SIZE",
    "PAGE_HEADER_FORMAT",
    "PAGE_HEADER_SIZE",
    "PAGE_HEADER_STRUCT",
    "SUPERBLOCK_FORMAT",
    "SUPERBLOCK_MAGIC",
    "SUPERBLOCK_PAGE_ID",
    "SUPERBLOCK_SIZE",
    "SUPERBLOCK_STRUCT",
]
