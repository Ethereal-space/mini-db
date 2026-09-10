# MiniDB 项目规格

## 1. 目标与依据

MiniDB 是四人协作完成的教学型页式数据库。核心链路必须真实贯通：

```text
SQL → Token → AST → Semantic → Bound AST → Logical Plan
    → Optimized Plan → Executor → TableHeap → BufferPool → Page → Disk
```

指导书的明确交付要求是底线，SQL 编译器课件用于细化词法、文法、AST、语义、计划、优化和测试，总体课件用于说明存储、执行与系统分层。引用方式和追踪矩阵见 `docs/references.md`。本文件中的精确接口、字节布局和边界是小组为消除材料歧义而冻结的项目约定。

## 2. 参考环境

| 项目 | 规则 |
| --- | --- |
| Python | CPython 3.14.2；`requires-python = ">=3.14,<3.15"` |
| pip | 25.3 |
| pytest | 9.1.1 |
| python-docx | 1.2.0，只用于生成手册，不是核心运行依赖 |
| Git | 参考 2.47.0.windows.2，最低 2.40 |
| GitHub CLI | 参考 2.92.0 |
| 编码 | UTF-8，并开启 Python UTF-8 模式 |
| 页面 | 4096 字节，整数按 little-endian |
| 核心依赖 | 仅 Python 标准库 |
| CI | Python 3.14.2，Windows 与 Ubuntu 24.04 |

生产代码不得使用 sqlite3、SQLAlchemy、Lark、PLY 或其他数据库/SQL 解析器替代实验实现。pytest 只用于开发测试。

## 3. 模块责任与依赖方向

| 成员 | 目录 | 责任 |
| --- | --- | --- |
| A | `minidb/frontend/`、`tests/frontend/` | Lexer、Parser、Token/AST 展示与前端扩展 |
| B | `minidb/compiler/`、`tests/compiler/` | Catalog 服务、名称绑定、类型检查、计划与优化 |
| C | `minidb/storage/`、`tests/storage/` | 磁盘页、槽页、行编码、缓冲池、表堆、Catalog 持久化 |
| D | `minidb/runtime/`、`tests/runtime/` | 表达式求值、执行算子、结果、Trace 与 CLI |
| 最终整合 | `minidb/integration/`、`tests/integration/` | 依赖装配、适配器、入口和端到端验收 |

`minidb/contracts/` 是唯一跨成员类型来源。成员模块只导入标准库、自身模块与 `minidb.contracts`，不能直接导入其他成员具体实现。返回包装的小差异只能在最终整合的 `minidb/integration/adapters.py` 处理，不能借整合修改冻结契约。

## 4. 核心 SQL 边界

核心支持四类单表语句：

- `CREATE TABLE`：列类型只有 `INT` 与 `VARCHAR`，至少一列；
- `INSERT`：可省略列列表，或给出包含全部列的显式列序；显式顺序可重排，但不得缺列或重复；
- `SELECT`：`*` 或列列表，单表，可选 `WHERE`；
- `DELETE`：单表，可选 `WHERE`；省略 WHERE 表示删除全部可见行。

标识符匹配 ASCII `[A-Za-z_][A-Za-z0-9_]*`，最长 64 个字符，比较时使用 `casefold()` 后的小写规范名，原词素仍保留。字符串允许中文，以两个连续单引号转义单引号。`INT` 是 32 位有符号整数；`VARCHAR` 上限是 255 个 UTF-8 字节。Python `bool` 不能当作 SQL `INT`。

核心 WHERE 支持 `= != < <= > >=`、`AND OR NOT` 与括号。INT 支持六种比较；VARCHAR 只支持等于与不等于；不做隐式类型转换。兼容词素 `==`、`<>` 分别规范化为 `=`、`!=`。Lexer 可以识别 FLOAT 与算术符号，但核心语义明确拒绝 FLOAT 存储值，一般算术属于扩展。

`NOT age = 18` 按冻结文法解析为 `NOT(age = 18)`。比较不允许链式书写，`a < b < c` 必须在第二个比较符处报语法错误。

## 5. 源位置、错误与 Trace

`Position.offset` 从 0 开始，按 Python 字符串中的 Unicode 码点计数；`line`、`column` 从 1 开始。`Span` 是左闭右开区间。CRLF 消耗两个码点但只增加一行，Tab 在核心规则中增加一列。

错误阶段使用固定大写名称：`LEXICAL`、`SYNTAX`、`SEMANTIC`、`PLANNING`、`OPTIMIZATION`、`EXECUTION`、`STORAGE`、`IO`、`UNSUPPORTED`。SQL 相关错误携带真实 Span；没有 SQL 位置的磁盘错误使用 `span=None`，并在 context 中提供页号、RID 或文件偏移。禁止用宽泛异常捕获把内部程序错误伪装成用户输入错误。

Trace 是单次调用的不可变快照。前端产生 TOKEN、AST 事件，编译器产生 SEMANTIC、BOUND、PLAN、OPTIMIZED_PLAN，运行时产生 EXECUTION，存储层可提供 BufferEvent/BufferStats。多语句执行不得把前一语句的事件串入后一语句结果。

## 6. 逻辑计划与执行语义

标准 SELECT 计划结构为：

```text
ProjectPlan(
  child = FilterPlan(child = SeqScanPlan(...), predicate = ...),
  indices = ...,
  names = ...
)
```

没有 WHERE 时省略 FilterPlan。DELETE 使用同表的 SeqScan 或 Filter 子树，扫描行必须保留 RID。CREATE 的表号和首页在执行阶段分配；编译阶段没有存储副作用。INSERT 的值在语义阶段按 schema 顺序重排并完成类型检查。

基础优化必须至少实现常量比较折叠和布尔化简，并证明优化前后结果等价。优化处理不可变 Plan/Bound 节点，不得执行用户数据访问或吞掉不合法输入。

应用对一个已成功解析的脚本逐句执行 `compile → optimize → execute`。因此同一脚本中成功 CREATE 后的 INSERT 能看到新 Catalog。词法或语法错误使整批解析失败且不执行；后续语义或执行错误停止剩余语句，已经成功的语句保留，不提供批次事务回滚承诺。

## 7. 页文件与持久化

核心数据库只有一个 `mini.db`。`page_id` 从 0 开始，文件偏移为 `page_id * 4096`。页 0 是 Superblock，页 1 是 Catalog 首页，新库初始 `page_count=2`。

Superblock 前 32 字节布局：

| 偏移 | 字段 | 格式 |
| ---: | --- | --- |
| 0 | magic | 8 字节 `MINIDB01` |
| 8 | version | u32，核心为 1 |
| 12 | page_size | u32，固定 4096 |
| 16 | page_count | u32 |
| 20 | free_head | i32，`-1` 表示无空闲页 |
| 24 | catalog_head | i32，初始为 1 |
| 28 | next_table_id | u32，初始为 1 |

数据页头固定 32 字节：magic `MDPG`、page_id、next_page_id、slot_count、free_start、free_end、flags、table_id、version、reserved。槽项固定 8 字节，保存记录 offset、length、flags。删除设置 tombstone，RID 不重新编号；基础实现不要求立即复用槽内空间。页面始终满足 `free_start <= free_end <= 4096`。

行编码必须与 ColumnMeta 顺序一致，INT 使用有符号 32 位 little-endian，VARCHAR 使用长度前缀与 UTF-8 字节。记录过大、损坏页、错误 table_id、越界 slot、重复 unpin 与 pinned frame 不可淘汰等情况必须明确报错。

BufferPool 必须实现可观察的 hit/miss/read/write/eviction，支持 LRU 与 FIFO，维护 pin count 和 dirty 标志。dirty 页只能在 flush 或淘汰时写盘；所有 frame 都 pinned 时取页失败，不能覆盖仍在使用的页。

Catalog 必须存入页链，不能以独立 JSON 文件充当核心持久化。写语句成功后 flush；正常关闭再打开后，表定义、数据、删除标志、页链和下一个 table_id 都能恢复。突然断电时的事务恢复属于 WAL 扩展。

## 8. 扩展规则

扩展通过 `ExtensionStatement`、`BoundExtension`、`ExtensionPlan` 交换 `{feature, version, payload, span}`。`feature` 使用非空小写规范名，`version>=1`，payload 只含 JSON 基本值以及嵌套 list/dict。扩展不得直接添加核心 AST、Bound AST、Plan 或 Protocol 字段。

扩展只有在用户明确声明、契约哈希一致、组内任务齐全且测试通过后才可整合。UPDATE、ORDER BY/LIMIT、JOIN、GROUP BY/聚合和 EXPLAIN 使用原子组；低耦合任务可逐项整合；缺少完整执行链的任务只保留代码与测试；页格式或系统实验默认不进入核心结果。权威规则见 `docs/integration/feature-groups.json`。

## 9. 任务与交付证据

`docs/tasks/catalog.json` 是 68 项任务的机器可读目录，卡片中的字段顺序由其 `field_order` 冻结。每项任务都要更新本成员 walkthrough 和 `delivery/member_<x>.json`，记录：完成任务、实际文件、测试命令与结果、契约哈希和讲解路径。

最终整合从 `_integration_input/member_a/` 至 `member_d/` 读取四份完整项目副本，输出到 `_integration_output/mini-db/`。成员 AI 与最终整合 AI 都不执行 Git 提交或推送；仓库不规定四人的提交、分支、同步或推送方式。
