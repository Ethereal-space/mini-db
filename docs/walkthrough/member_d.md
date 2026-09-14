# 成员 D 真实代码讲解

## 交付范围与快照

本次完成基础任务 D-B01 至 D-B06。运行时只导入 Python 标准库、`minidb.contracts` 和自身包；没有解析 SQL、查询 Catalog 私有状态、绕过 `StoragePort`，也没有修改冻结契约或整合层。

生产文件 SHA-256：

- `expression_evaluator.py`: `810c0c15b615260c8ce7e32efc9b27737d2a4f1b9b176456f395d8ecda8d550e`
- `operators.py`: `85347c58dcfc74134d1250ec20c2bf74ef7bf97b233754dfeee152bd56d93074`
- `command_executors.py`: `a81e9a90b0e844e30765427678f46a3899ac506ee6b912d5a08ade2fd7426fef`
- `execution_service.py`: `bba0e9cfad41f0f1b7667f0af12c7ce95538e8230669772cb3fd47c2d607487e`
- `result_formatter.py`: `6229866c5729603f83f45f8bf8873c9a796d6fbc3ff2843273e2174812173070`
- `cli_service.py`: `ff34d4763c4dd0a69e4ef8900a39d3a00260a12e88f308a5356396e232922070`

这些哈希对应当前整合后的生产代码；交互模式和查询摘要修复也包含在相应文件中。

## D-B01：BoundExpression 求值器

真实文件是 `minidb/runtime/expression_evaluator.py`，关键符号为 `evaluate`、`evaluate_unary`、`evaluate_binary`、`_require_bool` 和 `_matches_dtype`。

`evaluate(expr, values)` 按不可变节点类型递归分派。`BoundLiteral` 直接返回原值；`BoundColumn` 只使用已绑定的稳定 `index` 从行 tuple 取值，并检查下标与运行时类型；它不按列名查 Catalog。`BoundUnary` 只接受 `NOT`，操作数必须是实际 `bool`。`BoundBinary` 先处理 `AND/OR`：先算左侧，`False AND` 和 `True OR` 立即返回，右侧不会被访问；其余情况再求右侧。比较先检查两侧声明类型相同，再按 INT 六种比较或 VARCHAR 的 `=`、`!=` 白名单执行，不使用 `eval`，也不做字符串与整数转换。

代表输入 `age >= 18 AND NOT(name = 'Tom')` 对 `(7, "Tom", 22)` 的调用顺序是：读取 age、比较得到 True、读取 name、比较得到 True、NOT 得到 False、AND 得到 False。对左侧已经为 False 的 AND，递归在读取右节点前终止。

异常分支使用 `ExecutionError`：列越界为 `COLUMN_INDEX_OUT_OF_RANGE`，运行时列值破坏类型契约为 `COLUMN_TYPE_MISMATCH`，非 BOOL 布尔操作为 `BOOLEAN_REQUIRED`，混合类型比较为 `COMPARISON_TYPE_MISMATCH`，VARCHAR 排序为 `UNSUPPORTED_VARCHAR_COMPARISON`，未知节点为 `UNKNOWN_EXPRESSION`；错误保留原节点 Span 和下标、行宽等上下文。

测试位于 `tests/runtime/test_d_b01.py`：任务卡要求的 `test_db01_columns`、`test_db01_predicate`、`test_db01_short_circuit`、`test_db01_bad_column`、`test_db01_no_coercion` 均存在；另有全部六种整数比较、VARCHAR 拒绝和未知节点检查。

讲解题答案：短路必须发生在递归求右侧之前，因为右侧一旦求值，其中的错误或未来副作用已经发生，之后的条件判断无法撤销。

小修改结果：加入白名单别名 `== -> =` 和 `<> -> !=`；`test_db01_comparison_alias` 证明别名只改变运算符规范化，不放宽 B 层负责的类型规则。

## D-B02：SeqScan、Filter、Project 算子

真实文件是 `minidb/runtime/operators.py`。`RowOperator` 描述内部 `open/next/close` 形状；`SeqScanOperator`、`FilterOperator`、`ProjectOperator` 是三个实现；`build_operator` 从核心查询 Plan 递归构树。

`SeqScanOperator.open` 才调用 `storage.scan`，构造函数无数据库 I/O；`next` 每次最多交付一行，耗尽时关闭迭代器并稳定返回 None。`FilterOperator.next` 用循环跳过任意数量的不匹配行，因此 10000 行不匹配也不会递归溢出；谓词必须返回真正的 bool。`ProjectOperator.next` 根据 `indices` 创建新的 values tuple，允许重复下标并保留原 RID。三个算子的 `close` 都幂等，异常路径在重新抛出原错误前关闭子算子，关闭时的次生错误只附加为异常说明。

代表计划 `Project(Filter(SeqScan(student), age >= 20), name)` 从底向上 open，从上向下反复 next。17 岁行被 Filter 循环丢弃，20 和 22 岁行被 Project 转为单列新行，但 RID 保持不变；耗尽后 close 沿树向下传播。

测试位于 `tests/runtime/test_d_b02.py`：`test_db02_empty`、`test_db02_projection`、`test_db02_filter`、`test_db02_tree`、`test_db02_close` 分别验证空表、重复投影、固定栈深、组合结果和异常释放。

讲解题答案：Filter 的一次 next 可能需要跳过任意多行；循环保持固定栈深，并且每次只向父算子交付一个匹配行。

小修改结果：每个算子公开只读使用约定下的 `output_count` 计数；`test_db02_output_counter_resets_on_open` 证明重新 open 会归零且不改变输出。

## D-B03：建表与插入执行器

真实文件是 `minidb/runtime/command_executors.py`，本任务关键符号为 `execute_create`、`execute_insert` 和 `_io_error`。

CREATE 的可观察顺序固定为：`table_exists` 防御检查、`allocate_table_id`、`storage.create_table`、用真实首页构造 `TableMeta`、`catalog.register_table`、`storage.flush_all`，全部完成后才返回成功。INSERT 直接消费 `InsertPlan.table` 和已经按 schema 排好的 `values`，调用 `storage.insert` 得到真实 RID，再 flush，最后返回 `affected_rows=1`。D 层没有另建 JSON Catalog，也不猜首页号。

重复表在分配表号和创建页面前抛 `TABLE_ALREADY_EXISTS`。底层领域错误原样传播；原始 `OSError` 仅在明确的创建、插入、刷新边界转换为 `StorageIOError`，保留 `__cause__`、表名和原因。创建页后登记失败或写入后 flush 失败可能留下部分物理状态，代码不会宣称事务回滚。

测试位于 `tests/runtime/test_d_b03.py`：`test_db03_create_order` 精确断言调用顺序和 `TableMeta(1, student, ..., 2)`；`test_db03_duplicate`、`test_db03_create_io`、`test_db03_flush_fail` 覆盖错误边界；`test_db03_register_failure_stops_before_flush` 证明登记失败不继续 flush。

讲解题答案：CREATE 必须先创建页，因为完整 TableMeta 需要存储层返回的真实 `first_page_id`；该顺序避免登记不存在的页，但本身不提供原子性。

小修改结果：INSERT 成功结果加入 `EXECUTION` Trace，记录真实 `rid=page:slot`；`test_db03_insert` 同时证明普通结果仍为空列、空行且影响行数为 1。

## D-B04：删除执行器

真实文件仍是 `minidb/runtime/command_executors.py`，关键符号为 `execute_delete` 和只读内部方法 `preview_delete`。

`execute_delete` 对 `DeletePlan.child` 构造算子树，在 try/finally 中逐行拉取 `StoredRow`，把 `row.rid` 原样交给 `storage.mark_delete`，只有调用成功后才增加 `affected_rows`。扫描耗尽后关闭算子并 flush 一次；空匹配也 flush 一次并返回 0。相同 values 的行依靠不同 RID 独立删除，不按业务列反查，也不从正在迭代的列表中移除记录。

mark_delete 的领域错误原样传播；原始 OSError 转换为带表名、page_id、slot_id 的 `DELETE_IO`。任何异常都会关闭算子，不返回部分成功的 ExecutionResult；核心明确不承诺回滚已经完成的标记。

测试位于 `tests/runtime/test_d_b04.py`：`test_db04_partial`、`test_db04_all`、`test_db04_none`、`test_db04_equal_values` 和 `test_db04_failure` 覆盖部分、全部、空匹配、同值不同 RID 和第二次删除失败。

讲解题答案：DELETE 不能只拿 `(id,name)` 等值，因为结果值可能重复，也可能没有投影任何唯一列；RID 才能稳定定位物理页和槽。

小修改结果：`preview_delete` 复用同一算子树收集候选行，但不调用 mark_delete 或 flush；`test_db04_preview_is_read_only` 对两项调用次数都断言为 0。

## D-B05：执行服务、Trace 与结果格式

真实文件是 `minidb/runtime/execution_service.py` 和 `minidb/runtime/result_formatter.py`。关键符号为 `ExecutionService.execute`、`_execute_query`、`_finish_command`、`format_result` 和 `format_error`。

`ExecutionService.execute(plan)` 用明确的 `isinstance` 分支分派六类核心计划：CREATE、INSERT、DELETE 进入命令函数；SeqScan、Filter、Project 进入查询算子。查询从 Project 名称或底层表 schema 得到 columns，逐行复制 values 为结果 tuple，并在 finally 中关闭算子。`ExtensionPlan` 未注册时抛 `UnsupportedError(code="UNSUPPORTED_FEATURE")`，不会返回空成功。

每次 execute 创建新的事件 list，最终固化为 ExecutionResult.trace tuple。事件只使用 `EXECUTION` 阶段，依次记录 DISPATCH/OPEN、行 RID、CLOSE、行数和 BufferStats 快照；可选 trace sink 接收同一批不可变事件，但结果之间不共享列表。D 层不伪造 TOKEN、AST、SEMANTIC 或 PLAN 事件。

表格格式器保留列名，即使查询是 0 行也能显示 schema；字符串中的反斜杠、回车、换行和竖线以可辨形式转义。格式化是纯函数，不修改 ExecutionResult。错误格式器直接保留领域错误的 stage/code/位置/原因。

测试位于 `tests/runtime/test_d_b05.py`：`test_db05_select`、`test_db05_command`、`test_db05_trace`、`test_db05_unknown`、`test_db05_format` 覆盖查询形状、命令结果、连续调用隔离、未知扩展和中文/空结果/换行转义。

讲解题答案：columns 与 rows 分开后，空结果仍能显示 schema；查询和无列的命令也能共享同一个 ExecutionResult 契约。

小修改结果：`format_result(result, "json")` 只把结果快照转换为标准 JSON，使用 `ensure_ascii=False` 保留中文；`test_db05_json_format_round_trip` 用 `json.loads` 验证可往返。

## D-B06：ApplicationPort CLI

真实文件是 `minidb/runtime/cli_service.py`，关键符号为 `main`、`_build_parser` 和可注入的 `AppFactory` 类型。

`main(argv, app_factory, stdin, stdout, stderr)` 支持 `--db`、`--file`、`--interactive`、`--trace`、`--policy lru|fifo`、`--capacity` 和练习加入的 `--encoding`。capacity 在工厂调用前验证为正整数。指定文件时按明确编码读取；没有 `--file` 且 stdin 是终端时自动进入交互模式，也可以显式传 `--interactive`。交互循环按字符串/注释状态识别第一个真正的分号，支持多行 SQL 和 `.help`、`.quit`、`.exit`；一条语句的领域错误写入 stderr 后继续接收下一条。批处理路径仍只调用一次 `app.execute(source, trace)`，不会用 `split(';')` 破坏字符串或注释。工厂返回上下文管理器，正常、领域错误和 I/O 错误都经过 with 退出。

退出码为：成功 0；MiniDBError、文件和运行 I/O 错误 1；参数错误 2。只捕获这些预期错误，未知程序缺陷继续暴露。结果写 stdout，错误写 stderr，单元测试不会等待真实终端输入。

测试位于 `tests/runtime/test_d_b06.py`：`test_db06_stdin`、`test_db06_utf8`、`test_db06_trace`、`test_db06_error`、`test_db06_bad_capacity`、`test_db06_interactive_meta_commands_and_multiple_statements`、`test_db06_interactive_keeps_semicolon_inside_string_and_supports_multiline` 和 `test_db06_interactive_rejects_file_combination` 验证完整源码、中文、Trace、带第 3 行 Span 的语法错误、工厂调用次数、交互元命令、多语句和多行字符串。

讲解题答案：字符串和注释内部也允许出现分号，只有 Lexer/Parser 知道分号是否位于语句边界，因此 CLI 不能直接拆分。

小修改结果：`--encoding` 默认 UTF-8；`test_db06_encoding_error` 用 ASCII 读取中文 UTF-8 文件，验证返回 1、错误可读且数据库工厂没有被调用。结果输出的 Unicode 与源码解码是两个独立阶段。

## 可复现验证

```powershell
python -m pytest -q tests/runtime/test_d_b01.py
python -m pytest -q tests/runtime/test_d_b02.py
python -m pytest -q tests/runtime/test_d_b03.py
python -m pytest -q tests/runtime/test_d_b04.py
python -m pytest -q tests/runtime/test_d_b05.py
python -m pytest -q tests/runtime/test_d_b06.py
python -m pytest -q tests/contracts tests/runtime
python -m compileall -q minidb
python tools/generate_contract_hash.py --check
python tools/check_import_boundaries.py
python tools/check_utf8.py
python -m pytest -q
```

验证主机使用 CPython 3.14.5；仓库接受范围为 `>=3.14,<3.15`，但课程参考补丁版本是 3.14.2。pip 已对齐 25.3，pytest 已对齐 9.1.1。冻结契约摘要保持 `e086cafec0a281ca94d3141731d4b227369879ad3345cbae2653cf76f8f1aa3b`。
