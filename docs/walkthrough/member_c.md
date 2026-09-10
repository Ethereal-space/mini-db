# 成员 C 真实代码讲解

每完成一张任务卡，就按该卡的“代码讲解提示词”记录当前代码快照哈希、真实文件、类、函数、测试名、关键断言、正常路径、异常路径、不变量和小修改结果。

## 前置准备记录

本记录只说明成员 C 的开发环境已经就绪，不代表任何任务卡完成。

- 项目目录：`E:/SQL/mini-db`
- 本地虚拟环境：`.venv/`（已被 `.gitignore` 忽略，不进入仓库）
- Python：CPython 3.14.2
- pip：25.3
- pytest：9.1.1
- 开发依赖：按照 `requirements-dev.txt` 安装；包含 `python-docx==1.2.0`
- 项目安装：`minidb-course==0.1.0` editable 安装

已验证的命令：

```powershell
.\.venv\Scripts\python.exe -m compileall -q minidb tools tests
.\.venv\Scripts\python.exe tools\generate_contract_hash.py --check
.\.venv\Scripts\python.exe tools\check_import_boundaries.py
.\.venv\Scripts\python.exe tools\check_task_catalog.py
.\.venv\Scripts\python.exe -m pytest -q
```

结果：契约摘要一致、导入边界通过、68 项任务目录通过、29 个基线测试全部通过；`minidb.storage` 和冻结 `StoragePort` 均可导入。该准备记录完成时 `completed_tasks` 与 `verified_tasks` 仍为空，随后开始 C-B01。

## C-B01 真实实现讲解

### 实际修改范围与课程对应

C-B01 实际修改了 `minidb/storage/constants.py`、`minidb/storage/superblock.py`、`tests/storage/test_c_b01.py`、本文件和 `delivery/member_c.json`。没有修改 `minidb/contracts`、`contracts.sha256`、其他成员目录或 `integration`。

`constants.py` 固定了 `PAGE_SIZE=4096`、`SUPERBLOCK_FORMAT="<8sIIIiiI"`、`SUPERBLOCK_MAGIC=b"MINIDB01"`、`DATA_PAGE_MAGIC=b"MDPG"` 和两个 32 字节结构体。模块加载时断言 Superblock 和数据页头都是 32 字节，避免后续页读写代码静默采用错误布局。

### `Superblock` 的输入、输出和不变量

`Superblock` 是 frozen dataclass，字段是 `version`、`page_size`、`page_count`、`free_head`、`catalog_head` 和 `next_table_id`。构造时 `_check_u32`、`_check_i32` 拒绝布尔值、越界整数和错误的有符号范围，因此页数、链头和表号不会在 `struct.pack` 阶段才产生模糊异常。

`Superblock.initial()` 返回新库的固定值：`version=1`、`page_size=4096`、`page_count=2`、`free_head=-1`、`catalog_head=1`、`next_table_id=1`。`encode()` 把 magic 和六个字段按 little-endian 写入前 32 字节，并用零填充到完整 4096 字节；`decode()` 只读取输入字节，不写文件，先检查长度、保留区、magic、版本、页大小、页数、空闲链头、Catalog 首页和下一个表号。

`decode()` 的相对页号规则是：`free_head` 只能是 `-1` 或大于等于 2 的有效页号，不能占用 page 0 或 Catalog page 1；`catalog_head` 必须为 1。文件层的 `validate_file_size()` 再检查 `file_size == page_count * PAGE_SIZE`，所以 `page_count` 同时是分配边界和文件完整性边界。`from_bytes`/`to_bytes` 是同一编解码逻辑的可读别名，纯 round-trip 不产生副作用。

`describe_header()` 返回字段、偏移和值的字典：magic 在 0，version 在 8，page_size 在 12，page_count 在 16，free_head 在 20，catalog_head 在 24，next_table_id 在 28。它只访问 frozen 对象；`DatabaseFile.describe_header()` 只做打开状态检查后调用它，测试用文件 SHA-256 证明没有写入。

### `DatabaseFile.open()` 的正常路径

`DatabaseFile.open(path)` 先建立父目录并读取文件状态。路径不存在或长度为 0 时才使用 `w+b` 初始化：写入 `Superblock.initial().encode()`，再写入 `encode_data_page_header()` 产生的空 Catalog page 1，刷新并 `fsync`，最终文件恰为 8192 字节。数据页头固定为 `MDPG、page_id=1、next=-1、slot_count=0、free_start=32、free_end=4096、flags=0、table_id=0、version=1、reserved=0`。

已有非空文件使用 `r+b`，先读取 page 0，调用 `Superblock.decode()` 和 `validate_file_size()`，再读取 `catalog_head` 指向的数据页并由 `_validate_catalog_page()` 检查 magic、page_id、next 链头、空闲区间、版本和 Catalog 的 table_id。所有检查成功后才返回持有句柄的 `DatabaseFile`，不会重建或重新分配 page 1。

`read_page(page_id)` 只接受 `0 <= page_id < page_count`，按 `page_id * 4096` 定位并要求完整读取 4096 字节。`save_superblock()` 只写 page 0，禁止在 C-B01 中改变 page_count，并在写入前再次通过 `encode()`/`decode()` 校验；`update_superblock(next_table_id=7)` 使用 `dataclasses.replace` 生成新 frozen 值。`next_table_id` 属性没有 setter，供 B 的 CatalogService 读取持久化下界，而不是让 B 直接导入 storage。

上下文管理由 `__enter__`/`__exit__` 提供；`close()` 在刷新和 `fsync` 后关闭句柄，重复关闭安全。打开失败时 `_close_quietly()` 释放已经取得的句柄；格式错误保留为 `StorageFormatError`，操作系统错误转换为 `StorageIOError`，没有宽泛捕获 `Exception`。

### 测试证据与调用追踪

- `test_new_file_exact_layout`：通过 `DatabaseFile.open()` 创建临时库，断言 8192 字节、Superblock 七元组、page 0 保留区全零，并验证 `Superblock.decode(read_page(0))` round-trip。
- `test_catalog_page_bootstrap`：读取 page 1，逐字段断言 `DATA_PAGE_STRUCT` 的 MDPG 头和 4064 字节零填充。
- `test_reopen_preserves_bytes`：调用 `update_superblock(next_table_id=7)` 后保存字节，关闭并再次 `open()`，断言读出的表号为 7、Catalog 首页仍为 1、前后字节完全一致。
- `test_reject_corrupt_header_without_write`：分别破坏 magic、version、page_size 和截短文件；每种情况都断言 `StorageFormatError.stage == "STORAGE"`、`span is None`、context 含偏移/字段，且打开失败前后 SHA-256 不变。
- `test_id_initial_value_is_read_only`：断言 `next_table_id` 不能赋值，`max(4, database.next_table_id)` 得到 7；同时调用 `describe_header()` 并证明文件哈希不变，再验证错误没有 SQL Span。

代表性正常调用链是：`DatabaseFile.open` → `Superblock.initial` → `Superblock.encode` → `encode_data_page_header` → `fsync`；代表性失败链是：`DatabaseFile.open` → `_read_exact(page 0)` → `Superblock.decode` → 字段/长度校验 → `StorageFormatError`，句柄由 `_close_quietly` 释放且文件不写回。

本任务真实验证命令及结果：

```text
Python 3.14.2
pip 25.3
pytest 9.1.1
python -m compileall -q minidb                         PASS
python -m pytest -q tests/storage/test_c_b01.py        5 passed
python -m pytest -q tests/contracts tests/storage       25 passed
python tools/generate_contract_hash.py --check          PASS
python tools/check_import_boundaries.py                PASS
python tools/check_task_catalog.py                      PASS
```

C-B01 已完成并记录在 `delivery/member_c.json`。随后完成 C-B02 至 C-B06，下面的说明均对应当前工作树中的真实类、函数和测试。

## C-B02 真实实现讲解：DiskManager

### 文件与调用边界

C-B02 新增 `minidb/storage/disk_manager.py` 和 `tests/storage/test_c_b02.py`，并在 `superblock.py` 中补充了页追加、完整页覆盖和允许 page_count 前进的 `persist_superblock()`。`DiskManager` 只导入冻结错误类型以及 C 自己的 `constants`、`superblock`；没有导入其他成员实现。它接收 `DatabaseFile`、路径或兼容的缓存协调器，输出固定 4096 字节页和结构化存储错误。

### 分配路径

`allocate_page()` 首先读取页 0 的 `free_head`。如果链头不是 -1，函数检查它确实在 `_free_pages` 集合中，读取该页的 FREE 头，由 `_decode_free_page()` 得到后继，然后把该页整页清零并用 `persist_superblock()` 将链头前移。这样一个释放页只能被分配一次，且重新分配时看不到上一任的 payload。

如果没有空闲页，函数检查当前 `page_count` 是否仍能表示有符号页号，再调用 `DatabaseFile.append_page(bytes(PAGE_SIZE))`。`append_page()` 先确认文件长度等于旧页数乘 4096，写入页尾后刷新，再写 page 0 的新 `page_count`；追加失败时尽力截回旧文件长度。正常样例 `test_allocate_read_write_offsets` 证明新页号为 2、3，文件长度为 16384，字节分别出现在 8192 和 12288 偏移。

### FREE 链、释放和缓存协调

`free_page()` 先检查整数页号、范围、page 0/page 1 保护、重复释放以及调用者给出的 `live`/`linked` 前置条件。随后 `_reject_coordinator_in_use()` 读取协调器的 `pin_count`、`is_pinned`、`get_frame`、`is_live` 或 `is_linked`；任何仍在使用的状态都在写盘前返回 `PAGE_PINNED` 或 `PAGE_STILL_IN_USE`。如果底层页头仍是 `MDPG` 且 `slot_count>0`，也拒绝释放。

确认可释放后，先调用协调器的 `invalidate`/`invalidate_page`/`discard`，再将页写成 `FREE_PAGE_MAGIC=b"FREE"`、原页号、后继 free_head，其余字段和 4064 字节全部置零，最后持久化新的链头。`_free_pages` 只在两次写入都成功后更新；任一存储错误会尝试恢复原页。`test_free_cache_coordination_contract` 证明 pinned 页不改变链头，dirty 未 pin 页先失效，之后重新分配并写入新内容时旧 payload 不会回写。

`_load_free_list()` 在构造时从持久化链头重建集合，`free_list_chain()` 是只读的有限遍历工具，使用 `seen` 检测 2→3→2 环并抛出 `StorageFormatError(FREE_LIST_CYCLE)`。`test_duplicate_free_rejected_and_restart` 证明第二次释放失败且重启后的第一次分配只能得到一次 page 2；`test_free_page_header_is_strict` 证明 FREE 头或尾部非零会被拒绝。

### C-B02 真实验收证据

```text
python -m pytest -q tests/storage/test_c_b02.py       7 passed
python -m pytest -q tests/contracts tests/storage      55 passed（与 C-B03 至 C-B06 合并运行）
python -m compileall -q minidb                         PASS
```

## C-B03 真实实现讲解：Page 与 Slot

`minidb/storage/page.py` 中的 `Page` 使用精确 4096 字节 `bytearray`。页头由 `DATA_PAGE_STRUCT` 写入偏移 0、4、8、12、14、16、18、20、24、28；`Slot` 使用 `<HHHH`，分别保存记录 offset、length、flags 和 reserved。`Page.new()` 的空闲区间是 `[32,4096)`，槽目录从 32 向上增长，payload 从 4096 向下增长。

`insert(payload)` 先计算 `SLOT_SIZE + len(payload)` 与 `free_end-free_start` 的关系，容量不足时直接抛 `PageFullError`，因此失败前后字节完全不变。成功时记录起点是旧 `free_end-len(payload)`，写入 payload 和槽，再同步 `slot_count/free_start/free_end`。4056 字节的精确样例得到槽 0、offset 40、length 4056；4000 字节后插入 48 字节得到 offset 96 和 48，这由 `test_exact_fit_page` 与 `test_two_sided_growth` 直接断言。

`from_bytes()` 严格检查页长度、magic、page_id、next_page_id、版本、reserved、双端边界、槽区末端和每条记录范围，并按 offset 排序检查记录重叠。`read()` 遇 tombstone 抛 `RecordDeletedError`；`mark_deleted()` 只设置 flags bit 0，重复删除返回 False，不修改槽号、slot_count 或记录位置；`iter_live()` 复制字节后再返回，供表堆在 yield 前释放 pin。`live_count` 是逻辑存活数，`slot_count` 是物理槽数，`test_live_count_is_logical_not_physical` 证明删除只影响前者。

`test_tombstone_preserves_rid` 断言删除槽 1 两次分别为 True/False，活槽仍是 0、2，槽 2 的 offset 和 payload 不变；`test_serialize_roundtrip` 以 page 9、table 3、next 12 和中文字节验证完整 round-trip；`test_reject_corrupt_slot` 验证 free 区间、越界记录、记录重叠和非法槽号都不会返回截断记录。

## C-B04 真实实现讲解：RowCodec

`minidb/storage/row_codec.py` 的 `RowCodec.encode(schema, values)` 先检查列数，再按 schema 顺序逐列验证。INT 排除 Python `bool`，使用 `<i` 的有符号 little-endian 4 字节并检查 -2147483648 至 2147483647；VARCHAR 先 UTF-8 编码，以 `<H` 写入字节长度，最多 255 字节。所有字段验证并拼接后才检查单行 4056 字节上限，超限抛 `RowTooLargeError`，没有磁盘副作用。

`decode()` 维护 `cursor`，INT 读取 4 字节，VARCHAR 先读取 2 字节长度再严格 UTF-8 解码，每一步先检查剩余长度，结束时要求 cursor 等于 payload 长度。截断、非法 UTF-8、长度超过 255 或额外尾字节分别产生 `RowDecodeError`，上下文包含字段、列名或游标。`encoded_size()` 直接复用编码路径，保证大小计算不会与实际格式分叉。

`test_exact_binary_layout` 的真实输出是 `010000000500416c69636514000000`、长度 15；`test_int32_boundaries` 覆盖两端点和 bool；`test_utf8_byte_limit` 证明 85 个汉字正好 255 字节而 86 个失败；`test_decode_truncation_and_garbage` 和 `test_schema_order_and_types` 覆盖损坏及混合列顺序。C-B06 的超大行测试进一步证明 RowCodec 在任何页分配之前抛错。

## C-B05 真实实现讲解：Replacer 与 BufferPool

`Replacer` 用 `OrderedDict` 保存装入顺序和 `_pins` 计数。LRU 的 `record_access()` 将命中页移到末端，FIFO 命中不移动；`choose_victim()` 只返回 pin_count 为零的最早候选。`pin()`/`unpin()` 对每一次获取和释放计数，任何下溢都抛出明确错误。`LRUReplacer` 与 `FIFOReplacer` 是固定策略别名。

`BufferPool.fetch_page()` 是上下文管理器。命中时统计 hit、更新策略、pin 后 yield 原 Frame；未命中时先检查候选，所有帧 pinned 则抛 `BufferFullError`。有候选时先读取新页并校验 4096 字节，再对 dirty victim 调用 `_write_frame()`；写回成功后才从 Replacer 和映射中移除，失败则保留原 Frame、dirty 标志和映射，不会安装半成品新页。finally 分支始终解除 pin，因此调用者在 with 内抛异常也不会泄漏。

`mark_dirty()` 必须由修改 Frame 的调用者显式调用；`flush_page/flush_all()` 成功写回后才清除 dirty 并增加 writes；`invalidate()` 要求未 pin，丢弃释放页的旧 dirty 数据而不写回。`BufferEvent` 记录 hit/read/write/evict/invalidate，淘汰事件 detail 含策略和是否写回，`stats()` 返回冻结 `BufferStats`，`pinned_pages` 是只读现场检查。

`test_lru_fifo_a_b_a_c` 对访问 2、3、2、4 断言 LRU 淘汰 3、FIFO 淘汰 2，两个策略都是 1 hit、3 misses、1 eviction；`test_pin_and_exception_release` 证明全 pinned 时不覆盖 2/3，异常退出后 pin 为零；`test_dirty_eviction_survives_reopen` 证明 dirty page 2 在淘汰前写一次并能重启读回；`test_failed_write_preserves_frame` 证明写回故障保留原 dirty 帧；`test_free_invalidates_dirty_cache` 将真实 BufferPool 作为 C-B02 协调器，证明释放后旧帧不再回写。

## C-B06 真实实现讲解：TableHeap 与 PageCatalogRepository

### TableHeap 的物理路径

`TableHeap` 可以接收路径、`DatabaseFile`、`DiskManager` 或已有 `BufferPool`。它只把后三层连接起来，不解析 SQL。`create_table(table_id, columns)` 先校验正整数 ID、ColumnMeta 和连续 ordinal，调用 `DiskManager.allocate_page()`，用 `Page.new()` 写入 table_id，再推进并持久化 `next_table_id`，最后 flush。

`insert()` 的第一步是 `RowCodec.encode()`，所以超大行在任何页分配前失败。随后 `_insert_descriptor()` 用 `seen` 遍历 next_page_id 链：在 with 中解析 Page、确认 table_id 并尝试插槽；空间不足时离开 with，再追加新页、写入记录，重新获取旧页并连接 next_page_id。这个顺序使 pool_size=1 时也不会同时 pin 旧页和新页。链环、页号越界、table_id 不符都会转为带 page_id 的 `StorageFormatError`。

`scan()` 复制当前页的 live `(slot_id,payload)` 列表和 next_page_id，离开 BufferPool with 后才逐行解码并 yield `StoredRow(RID(...), values)`。因此调用者暂停在 yield 上时没有持有页 pin。`mark_delete()` 先遍历并验证 RID.page_id 属于目标表链，再由 Page 设置 tombstone；跨表或不存在槽的 RID 抛 `RID_NOT_IN_TABLE`，不会修改目标表。

`test_thousand_rows_cross_pages` 使用 166 字节行验证 23 行/页、44 个数据页、1000 个唯一 RID 和 pin=0；`test_scan_early_stop_unpins` 在只 next 一行后加载另一页，容量 1 仍成功；`test_oversize_row_before_allocation` 对 1015 个 INT 断言 RowTooLargeError、page_count、页链和文件字节全部不变。

### 页式 Catalog

`PageCatalogRepository` 内置固定六列：`table_id INT`、`table_name VARCHAR`、`first_page_id INT`、`column_ordinal INT`、`column_name VARCHAR`、`column_type VARCHAR`。它把 Catalog 当作 table_id=0、首 page 1 的普通页链，调用 TableHeap 的 `insert_raw/scan_raw/mark_delete_raw`，没有 JSON、pickle 或旁路文件。

`save_table()` 先扫描并 tombstone 同 table_id 的旧记录，再按列 ordinal 写新记录并 flush。`load_tables()` 按 table_id 分组，检查 table_name/first_page 一致、名称小写规范、ordinal 唯一且从 0 连续、列类型属于 INT/VARCHAR、表名不重复，最后重建不可变 `TableMeta`。目录损坏不会返回残缺 schema。

`test_delete_and_restart` 证明删除中间槽后关闭所有服务再打开，Catalog schema、首数据页和 tombstone 均保持；`test_catalog_uses_pages_and_no_sidecar` 用 17 张六列长名表使 Catalog 跨页，并逐条重新用 RowCodec 解码；`test_reject_wrong_rid_and_catalog_corruption` 证明跨表删除被拒绝以及 ordinal 0、2 的目录被报告为 `StorageFormatError`。

## 成员 C 最终真实验证

当前工作树执行结果：

```text
Python 3.14.2
pip 25.3
pytest 9.1.1
python -m compileall -q minidb                         PASS
python -m pytest -q tests/storage/test_c_b01.py        5 passed
python -m pytest -q tests/storage/test_c_b02.py        7 passed
python -m pytest -q tests/storage/test_c_b03.py        6 passed
python -m pytest -q tests/storage/test_c_b04.py        6 passed
python -m pytest -q tests/storage/test_c_b05.py        5 passed
python -m pytest -q tests/storage/test_c_b06.py        6 passed
python -m pytest -q tests/contracts tests/storage       55 passed
python -m pytest -q                                     64 passed
```

静态检查还包括 `tools/generate_contract_hash.py --check`、`tools/check_import_boundaries.py`、`tools/check_task_catalog.py` 和 `tools/check_utf8.py`；均通过。冻结契约哈希仍为 `e086cafec0a281ca94d3141731d4b227369879ad3345cbae2653cf76f8f1aa3b`。C-B01 至 C-B06 的生产代码均没有 sqlite3、SQLAlchemy、Lark、PLY、pass、TODO、FIXME、dummy row 或测试专用假返回。

现场口述练习：解释 `DatabaseFile.append_page()` 为什么必须先让物理长度与新 page_count 一致，再写 page 0；解释 `Page.insert()` 为什么把新 Slot 的 8 字节计入容量；解释 `RowCodec.decode()` 为什么拒绝尾字节；解释 `BufferPool.fetch_page()` 为什么在写回成功之后才删除 victim；解释 `TableHeap.scan()` 为什么把 `iter_live()` 结果复制后才 yield。小修改已经实现为 `free_list_chain()`、`Page.live_count`、`RowCodec.encoded_size()` 和 `BufferPool.pinned_pages`，每个都有对应测试或现场可检查结果。
