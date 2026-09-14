# 成员 B 真实代码讲解

## B-B01 CatalogService

本任务实现 `minidb/compiler/catalog_service.py` 中的 `CatalogService`，只依赖冻结的 `TableMeta`、`normalize_identifier`、`CatalogPort`、`CatalogRepositoryPort`、`SemanticError` 和 `StorageError`。

构造服务时调用 Repository 的 `load_tables()` 一次，把表名映射到不可变的 `TableMeta`，同时维护已使用的表号集合。已加载表的最大表号加一与 `initial_next_table_id` 取较大值，得到下一候选表号。空 Catalog 从 1 开始；C 的 Superblock 在最终整合时通过 `initial_next_table_id` 提供持久化下界。

`table_exists` 和 `get_table` 先用冻结契约的 `normalize_identifier` 做 ASCII 标识符规范化，再访问内存索引。`get_table` 找不到表时抛出 `SEMANTIC/TABLE_NOT_FOUND`，而不是访问 Repository。`list_tables` 按 `table_id` 排序并返回新列表，调用方清空返回值不会改变缓存。

`allocate_table_id` 只推进服务内的候选号，不写 Repository，也不注册表。表号超过 2147483647 时抛出 `IO/ID_EXHAUSTED`。`register_table` 先检查表号、规范表名、列存在、列序连续和列名唯一，再检查缓存中的表名和表号冲突；只有 `save_table` 成功后才发布到内存索引，并推进高水位。

## 测试证据

`tests/compiler/test_b_b01.py` 使用本地 `FakeRepository`，记录 `load`/`save` 次数并可注入保存异常。覆盖：

- `test_empty_catalog_allocates_unique_ids`
- `test_reload_normalizes_and_preserves_schema`
- `test_register_rejects_duplicate_before_save`
- `test_list_tables_returns_stable_table_id_order`
- `test_repository_failure_does_not_publish_cache`
- `test_register_then_reload`
- `test_catalog_id_high_water`

保存失败测试确认原始 `StorageError` 向上层传播，且失败的新表不会出现在缓存；重启测试确认 Fake Repository 中的数据可再次加载。测试文件和生产代码已通过 `compileall` 与 `git diff --check`。在项目虚拟环境 Python 3.14.7 和 pytest 9.1.1 下，B-B01 的 7 个测试全部通过，`tests/contracts tests/compiler` 共 32 个测试通过。

## 理解题和小修改

不能先更新缓存再调用 `save_table`。否则保存失败时内存会错误地宣称表已存在，而重启后 Repository 中没有该表。实现采用“验证 → 持久化 → 发布缓存”顺序；这不宣称底层 I/O 具有事务原子性。

额外加入 `test_list_tables_returns_stable_table_id_order`，以 9、2 的加载顺序断言输出为 2、9。它只验证服务返回列表的排序，不改变 `CatalogRepositoryPort` 接口。

## 与其他成员的连接

当前测试数据由本任务内的 `FakeRepository` 提供，不读写 `mini.db`。

- 与 C：最终整合时，C-B06 的页式 Catalog Repository 替换 Fake；C-B01 的 `Superblock.next_table_id` 通过 `initial_next_table_id` 注入。B 不导入 C 的实现。
- 与 D：D-B03 在执行 CREATE 时调用 B 的 `allocate_table_id`，完成 C 的建表后构造 `TableMeta`，再调用 B 的 `register_table`；B-B01 不执行 CREATE，也不调用 Storage。
- 与 A：后续 B 的语义任务接收 A 产生的 AST；B-B01 本身只接收 `TableMeta` 和 Repository，不依赖 A 的 Parser。
- 与整合层：整合层负责把 A/B/C/D 的真实实现装配起来；本任务只负责 B 模块、compiler 测试、walkthrough 和 delivery 记录。

## B-B02 CREATE 与 INSERT 语义检查

本任务实现 `minidb/compiler/semantic.py` 中的 `analyze_create`、`analyze_insert`、`bind_insert_columns` 和 `validate_literal`。输入是手工构造的冻结 AST 与 `CatalogPort`，输出是 `BoundCreate` 或 `BoundInsert`；实现不分配表号、不注册 Catalog、不访问 Storage。

`analyze_create` 先规范化表名并查询 `catalog.table_exists`，然后按声明顺序检查列名重复和 `INT`/`VARCHAR` 类型，生成 ordinal 从 0 连续递增的 `ColumnMeta`，最后构造 `BoundCreate`。`analyze_insert` 通过 Catalog 获取 `TableMeta`，把验证委托给 `bind_insert_columns`。没有显式列名时按 schema 顺序解释值；有显式列名时拒绝重复列和未知列，要求列集合与 schema 完全相等，再按 `ColumnMeta.ordinal` 重排值。

`validate_literal` 严格拒绝隐式转换：INT 排除 Python bool 并检查 -2147483648 到 2147483647；VARCHAR 按 UTF-8 字节数检查不超过 255。所有语义错误都带有对应的源 Span；错误发生在任何写操作之前。

测试文件 `tests/compiler/test_b_b02.py` 使用 FakeCatalog，覆盖：`test_create_validates_without_mutation`、`test_insert_reorders_explicit_columns`、`test_insert_without_columns_uses_schema_order`、`test_insert_rejects_duplicate_unknown_and_missing_columns`、`test_literal_boundaries_and_utf8_length`。使用 Python 3.14.7 和 pytest 9.1.1 时，B-B02 测试 5 项全部通过，`tests/contracts tests/compiler` 共 32 项通过。

与其他成员的连接：A-B03 将来提供等价的 CREATE/INSERT AST；B-B05 将 `BoundInsert` 放入 `InsertPlan`；D-B03 只执行已按 schema 顺序排列的值；C 不参与本任务的独立测试。

## B-B04 类型推导与 Bound AST

本任务在 `minidb/compiler/semantic.py` 中实现 `bind_expression`、`infer_binary` 和 `require_predicate`。`bind_expression` 根据 AST 节点类型递归处理：Literal 根据真实 Python 值推导 INT/VARCHAR，Identifier 在 `TableMeta.columns` 中解析为带 ordinal、name、dtype 和原 Span 的 `BoundColumn`，UnaryExpr 只接受 NOT，BinaryExpr 先绑定左右子树再由 `infer_binary` 应用规则并生成 `BoundBinary`。

核心规则是 INT 支持六种比较，VARCHAR 只支持等号和不等号；比较结果为 BOOL；AND/OR 两侧和 NOT 操作数必须为 BOOL。FLOAT、None、外来字面量和未知运算符都报告 `SEMANTIC` 错误。`require_predicate` 额外保证 WHERE 根表达式是 BOOL。实现没有调用 D 的求值器，也没有提前折叠表达式，因此 `1=0 AND missing_column=1` 仍会先检查并报告缺列。

`tests/compiler/test_b_b04.py` 使用手工 AST 和 `TableMeta`，包含 `test_integer_comparison_matrix`、`test_string_equality_and_type_mismatch`、`test_not_preserves_comparison_subtree`、`test_where_rejects_non_boolean_and_float`、`test_binding_checks_unreachable_branch`。测试断言 Bound 节点类型、dtype、运算符、Span、列 index、错误阶段和错误位置；输入 AST 保持不变。

验证命令：Python 3.14.7、pip 26.2.1、pytest 9.1.1 环境下，`python -m pytest -q tests/compiler/test_b_b04.py` 为 5 passed，B-B01/B-B02 回归为 12 passed，`tests/contracts tests/compiler` 为 37 passed，`compileall` 和 `git diff --check` 均通过。

本任务的独立测试不连接 A、C、D。整合时 A 的 Parser 提供等价 AST，D-B01 对相同 Bound 规则求值；扩展 FLOAT/NULL 必须走独立扩展契约，不能修改冻结核心 DataType。

## B-B05 Logical Plan 与只读编译管线

本任务实现 `minidb/compiler/planner.py` 中的 `build_plan` 和 `Compiler`，并在 `semantic.py` 中补充 B-B05 编译门面所需的最小 `analyze_select`、`analyze_delete` 入口。`build_plan` 只接收合法的 Bound 语句，按类型分派为不可变 Plan，不重新解析列名、不再次重排 INSERT 值，也不调用 Storage。

计划结构如下：CREATE → `CreateTablePlan`；INSERT → `InsertPlan`；无 WHERE 的 SELECT → `ProjectPlan(SeqScanPlan)`；有 WHERE 的 SELECT → `ProjectPlan(FilterPlan(SeqScanPlan))`；无 WHERE 的 DELETE → `DeletePlan(SeqScanPlan)`；有 WHERE 的 DELETE → `DeletePlan(FilterPlan(SeqScanPlan))`。DELETE 树不包含 Project，因此执行器仍能从 SeqScan 获得带 RID 的 StoredRow。

`Compiler.compile` 固定按 AST 语句类型执行语义分析，再调用 `build_plan`；语义成功后通过可选 `on_trace` 回调发出 `SEMANTIC`、`BOUND` 和带树形内容的 `PLAN` 事件。语义失败会原样传播，不调用 `build_plan`，也不会发出成功事件。CREATE/INSERT 编译阶段只读 Catalog，不分配表号、不注册表、不写 Storage。

测试文件 `tests/compiler/test_b_b05.py` 使用手工 Bound AST、FakeCatalog 和 TraceEvent 回调，覆盖：`test_select_plan_shape_with_and_without_filter`、`test_insert_plan_contains_schema_order_values`、`test_create_plan_does_not_allocate_identity`、`test_delete_plan_retains_rid_path`、`test_compile_stops_after_semantic_failure`。测试使用树形 `isinstance` 和字段断言验证结构、表元数据、值顺序、Span、RID 路径和调用次数。

验证命令：Python 3.14.7、pip 25.3、pytest 9.1.1 环境下，B-B05 测试 5 项通过，B-B01/B-B02/B-B04 回归 22 项通过，`tests/contracts tests/compiler` 共 42 项通过，`compileall`、`git diff --check` 和禁止依赖扫描均通过。

理解题：计划树按执行依赖从下向上组织。Project 要从 Filter 取得合格行，Filter 要从 SeqScan 取得原始行，所以 SQL 的书写顺序不会直接变成同顺序的嵌套。小修改练习已由 `test_delete_plan_retains_rid_path` 覆盖：DELETE 无 Project，若误加 Project 会破坏 DELETE 的 RID 保留路径并导致该结构断言失败。

独立测试使用 FakeCatalog；整合时 A.parse 的 AST 进入 `Compiler.compile`，再单独交给 B-B06 的优化器，最后由 D Executor 执行。C 的真实存储不在本任务中接入。

## B-B06 常量折叠、布尔化简与计划格式化

本任务新增 `optimizer.py` 的 `fold_constants`、`simplify_boolean`、`optimize`、`OptimizationReport` 和 `RuleHit`，以及 `plan_formatter.py` 的 `format_expression`、`format_plan`。输入是通过 B-B04 语义检查的冻结 `BoundExpr`/`PlanNode`，输出是新建的优化树，不读写数据页。

`fold_constants` 自底向上重建表达式，仅对同一 `INT` 或 `VARCHAR` 类型的字面量比较求值，结果为采用比较节点原 Span 的 BOOL `BoundLiteral`。`simplify_boolean` 重建受影响节点，应用 `TRUE AND p`、`p AND TRUE`、`FALSE OR p`、`p OR FALSE`、常量 `AND/OR` 和 `NOT`；核心没有 NULL，所以没有三值逻辑分支。每次规则改写都记录 `RuleHit(rule_name, before, after)`，报告按规则名计数。

`optimize` 先递归 child，再优化 Filter 谓词。恒真 Filter 返回 child 并记录 `filter_true_removed`；恒假 Filter 仍保留为 `FilterPlan`，不新增 `EmptyPlan`。Project、Delete 和子计划均按字段重建，Create/Insert/SeqScan 与版本化 `ExtensionPlan` 保持不变。`format_plan` 按固定缩进和字段顺序输出算子、表名、列索引、列名和表达式，不包含对象地址。

`tests/compiler/test_b_b06.py` 的 5 项测试覆盖：`test_constant_comparison_folding` 验证三个常量比较、BOOL 值、原 Span 和精确命中次数；`test_two_rules_transform_demo_plan` 验证 Project/Filter 组合改写且原树仍含 AND；`test_true_filter_removed_false_filter_kept` 验证恒真消除、恒假保留；`test_optimizer_equivalence_and_idempotence` 用独立参考求值器验证四行数据优化前后 id 均为 `(1, 4)`、二次优化相等且原计划未变；`test_plan_format_is_stable_and_semantic_errors_survive` 验证稳定格式与 `age#2`，并断言缺列错误仍是带 Span 的 `SEMANTIC` 错误。

必须先做完整语义检查再优化：`p` 可能引用不存在的列或有非法类型，提前消去 `FALSE AND p` 会错误扩大 SQL 的可接受集合。小修改练习 `p OR FALSE -> p` 已实现并记录 `boolean_simplify`，返回左子树且不改旧树。整合时由 D 在同一真实数据上执行原始和优化计划并展示 Trace；本卡仍只使用手工 Bound 节点和 FakeCatalog，不连接 A、C、D 的真实实现。

## B-B03 SELECT 与 DELETE 名称绑定

本任务在 `semantic.py` 中新增 `resolve_column`、`bind_projection`、`bind_select` 和 `bind_delete`。`resolve_column(name, table, span)` 先规范化名称，再按 `TableMeta.columns` 查找，返回包含 `index=ordinal`、规范名、类型和原 Span 的 `BoundColumn`。未知列抛出带表名、列名和对应标识符 Span 的 `SEMANTIC/UNKNOWN_COLUMN`，不会访问数据行或产生计划副作用。

`bind_projection` 对 `columns is None` 按 schema 顺序展开星号；显式列逐项解析，保持用户顺序和重复项。`bind_select` 先只读获取表，再绑定投影和 WHERE；WHERE 复用 B-B04 的 `bind_expression`，因此 `age` 永远绑定原表 index 2，不受 SELECT 投影影响。`bind_delete` 复用同一绑定路径但不产生 Project，并保留 `BoundDelete.table` 的 `table_id` 与 `first_page_id`。

为兼容 B-B05，`analyze_select` 和 `analyze_delete` 现在转调上述入口；`bind_expression` 的 Identifier 分支也统一调用 `resolve_column`。冻结 AST 没有被原地替换。`tests/compiler/test_b_b03.py` 使用写方法调用即失败的 FakeCatalog，5 项测试分别覆盖列 ordinal/type/Span、星号展开、显式投影顺序与重复、缺表缺列错误位置，以及 DELETE 原表元数据和条件 index=2。

理解题：不能用字典表示 `SELECT name,name`，因为字典键唯一，会丢失重复输出项；有序 tuple `indices/names` 才能同时保留顺序和重复。小修改练习 `SELECT age,id` 应得到原行索引 `(2, 0)` 与名称 `('age', 'id')`，且原 AST 不变。整合时 A 提供等价 AST，B-B05 构造计划，D 按原行索引执行和投影，D-B04 使用保留的 RID；本卡独立测试不连接其他成员真实实现。
