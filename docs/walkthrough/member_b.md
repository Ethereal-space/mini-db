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

## B-E01 可注册优化规则框架

本任务新增 `minidb/compiler/rules.py` 的 `Rule`、`RuleOptimizer`、`RuleTrace` 和 `RuleTraceEntry`，并把 B-B06 的常量比较与布尔化简包装为 `optimizer.py` 中的两个默认规则。`Rule` 是不可变描述，包含稳定 `name`、`priority` 和 `apply(PlanNode)->PlanNode`；`RuleOptimizer.register` 拒绝重名，`rules` 按 `(priority, name)` 排序，因此逆序注册不会改变执行顺序。

`RuleOptimizer.run` 每轮按选中规则执行，使用 Plan 的结构相等性比较变化，而不是比较对象地址；整轮没有变化即达到固定点。`enabled_names` 可以只启用指定规则，未知名称立即报错。超过 `max_rounds` 时抛出 `OptimizationError`，阶段为 `OPTIMIZATION`、代码为 `ITERATION_LIMIT`，context 包含最后规则名和轮次上限。规则 apply 的真实异常直接传播，不伪装成优化成功。`RuleTrace` 只在前后结构不等时记录规则名、轮次、稳定的计划前后文本和变化数。

`optimizer.optimize` 保持 B-B06 原调用形式，并增加 `enabled_names`、`max_rounds`、`rules` 选项；默认注册 `constant_comparison`（优先级 10）和 `boolean_simplify`（优先级 20），完成后把 RuleTrace 转换为已有 `OptimizationReport`。只启用常量规则时，`TRUE AND age>=18` 保持不变且没有布尔命中；启用两条规则时仍得到 `age>=18`，原始不可变计划不变。

测试 `tests/compiler/test_b_e01.py` 的 4 项测试分别验证默认规则结果与 Trace、显式规则选择、同优先级确定性和重名拒绝、振荡规则在 `max_rounds=3` 后停止并报告结构化错误。测试使用冻结 Plan 与测试内自定义 Rule，不依赖 Catalog、Storage 或其他成员模块。

理解题答案：规则可能互相暴露新的优化机会，所以需要继续运行到固定点；优先级和名称排序保证结果可重现；轮次上限保护错误的振荡规则，避免优化器无限循环。小修改练习对应 `enabled_names={"constant_comparison"}`：关闭规则后布尔规则没有命中日志，且输出保持 `TRUE AND age>=18`。

整合边界：这是 B 内部可选扩展，不改变 `CompilerPort`、冻结 Plan 或其他成员接口；集中整合只选择已经验收的 B 扩展分支，D 仍负责真实数据上的执行验证。

## B-E02 Projection Pruning 与冗余节点删除

本任务新增 `minidb/compiler/projection_pruning.py` 的 `required_columns`、`compose_projects` 和 `prune_plan`，并新增 `docs/extensions/b_projection_pruning_v1.json` 快照说明。输入是冻结的 SELECT `ProjectPlan`/`FilterPlan`/`SeqScanPlan`；输出是 `projection_pruning/v1` 的冻结 `ExtensionPlan`，或者对 DELETE 原样返回原计划。实现不改变核心 SeqScan 的全行布局，不读写数据页，也不导入 D 的执行器。

`required_columns` 对 Bound 表达式递归收集 `BoundColumn.index`；对计划从 Project 输出需求向下传播，遇到 Filter 时并入谓词列引用，最后返回排序且去重的原表序号。因此 `SELECT name WHERE age>=18` 需要 `(1, 2)`，而不是只需要投影列 `(1)`。`compose_projects` 将外层索引映射到内层索引，保留外层名称、顺序和重复项；宽度或越界索引违反计划契约时抛出 `PlanningError`。

`prune_plan` 先安全删除恒真 Filter 并合并相邻 Project，再建立旧序号到新序号的 `index_map`。它递归重绑 predicate 中所有 `BoundColumn`，同时重写 projection indices，最终 payload 使用 JSON 基本值记录 `columns`、字符串键 `index_map`、`child`、`predicate` 和 `projection`。缺失必需列或缺失 SeqScan 不会静默跳过，而是报告规划错误。DELETE 路径明确禁用裁剪并保持 RID 所需的原计划结构。

测试 `tests/compiler/test_b_e02.py` 的 4 项测试覆盖：`test_required_columns_include_predicate` 验证投影与 WHERE 的列并集；`test_project_composition_preserves_duplicates` 验证相邻 Project 的 `(1,1,2)` 映射和重复名称；`test_pruning_rebinds_every_index` 验证 `1→0`、`2→1`、谓词和投影同时重绑；`test_pruned_plan_matches_reference` 使用独立参考解释器在 Alice/Bob 两行上验证原计划与快照都输出 `[('Alice',)]`，并断言 DELETE 未裁剪。

理解题答案：投影列集合不足以决定扫描列，因为 Filter、Join、Sort 等下游算子可能引用不输出的列；本任务至少把 Filter 谓词引用合并进需求集合。小修改练习是让 WHERE 同时引用 `id` 和 `age`，需求应扩展到 `(0,1,2)`，实现中的 `_expression_columns` 递归并集逻辑已经支持该情况。

整合边界：B 独立完成需求分析、重绑定、快照和安全冗余删除；真实裁剪执行需要 D 增加对 `projection_pruning/v1` 布局的执行支持后才能进入集中整合，当前不能宣称端到端裁剪已启用。

## B-E03 算术表达式类型检查与常量折叠

本任务新增 `minidb/compiler/arithmetic.py` 的 `validate_payload`、`bind_arithmetic` 和 `fold_arithmetic`，以及 `docs/extensions/b_arithmetic_v1.json`。扩展使用冻结 `BoundExtension(feature='arithmetic', version=1, payload, span)`，不修改核心 Bound AST、Plan 或 Protocol。节点 payload 由 `kind`、`fields` 和可选源 Span 组成，支持 literal、column、binary 三种节点。

`validate_payload` 检查 kind、fields 及 binary 所需的 `op/left/right`；`bind_arithmetic` 递归处理节点：字面量必须是非 bool 的 INT，列名通过 B-B03 的 `resolve_column` 绑定到 ordinal/name/dtype，二元操作只接受 `+/-/*` 且两侧最终都为 INT。每个纯常量二元节点在绑定时先检查 INT32 范围，错误携带对应运算 Span；`/` 明确报告 `UNSUPPORTED_OPERATOR`。

`fold_arithmetic` 自底向上重建 payload，只有左右均为 literal 时才计算，否则保留 binary 结构，不读取行数据。加、减、乘每一步都检查 `[-2147483648, 2147483647]`，结果仍封装为 arithmetic/v1 的 INT literal；原 BoundExtension 和原始输入字典不被修改，重复折叠结构稳定。这样 `10+8` 可得到 18，再由核心比较形成 `age>18`；`age+1` 不能被折叠，因为列值随行变化。

测试 `tests/compiler/test_b_e03.py` 的 4 项测试覆盖：`test_fold_course_example` 验证 10+8、Span 和 17/18/19 的比较结果；`test_fold_nested_arithmetic` 验证 `(2+3)*4-1=19`、输入不变和幂等；`test_arithmetic_rejects_overflow_and_types` 验证溢出、非 INT 和 `/` 的错误 code/Span；`test_arithmetic_predicate_equivalence` 用独立参考计算验证优化前后结果均为 `[False, False, True]`。

理解题答案：`age+1` 的结果取决于当前行，编译阶段没有行值，因此只能保留该非纯常量子树，不能错误折叠成一个常量。小修改练习是加入一元负号并测试最小 INT 取负溢出；当前 v1 没有声明该运算符，因此不会把它伪装成已支持能力。整合时 A-E07 提供对应语法，D 需要实现同版本 arithmetic BoundExtension 求值后才能宣称完整 SQL 链路可用。

## B-E04 UPDATE 语义检查与更新计划

本任务新增 `minidb/compiler/update_planner.py` 的 `bind_assignments`、`validate_update` 和 `build_update_plan`，以及 `docs/extensions/b_update_v1.json`。UPDATE 使用 `update/v1` 的冻结 `ExtensionPlan` 快照，不扩展冻结 AST。`validate_update` 从节点 fields 读取表、assignments 和 where，通过只读 Catalog 获取 `TableMeta`；缺表报告 `SEMANTIC/TABLE_NOT_FOUND`。

`bind_assignments` 先逐项调用 B-B03 `resolve_column`，以目标 ordinal 检查重复列，第二次赋值直接在其目标 Span 抛出 `DUPLICATE_UPDATE_COLUMN`，不会先放入字典再覆盖。之后用 B-B04 `bind_expression` 绑定右值，要求右值 dtype 与目标列完全相等；绑定完成后输出固定 `{index, expr}` 项并按目标 index 排序。右值列引用保留旧行 index，所以 `SET a=b,b=a` 会得到 0→1、1→0，执行器可以同时从同一旧行求值。

`build_update_plan` 将 assignments、where 和 child 写入 JSON 兼容 payload。没有 WHERE 时 child 是全表 `SeqScan`；有 WHERE 时是 `Filter` 包住 `SeqScan`。两种路径都不生成 Project，确保后续删除/更新仍能获得 RID；绑定和计划构造不分配表号、不注册表、不访问 Storage。

测试 `tests/compiler/test_b_e04.py` 的 5 项测试覆盖：`test_update_binds_assignments_by_schema` 验证 age/name 按 index 排序、WHERE id index=0 和 RID child；`test_update_duplicate_assignment_rejected` 验证第二次赋值的错误 code/Span；`test_update_type_and_unknown_column` 验证类型与未知列错误及 SET 位置；`test_update_without_where_has_full_scan` 验证无 Filter/Project 的 SeqScan 和版本 1；`test_update_column_swap_binding` 验证交换赋值引用旧行索引且没有写调用。

理解题答案：重复 SET 列必须在转字典前检查，因为字典会覆盖旧项，丢失用户错误和第二个赋值位置。小修改练习已由 `test_update_column_swap_binding` 完成，两个 Bound 列分别指向旧行 index 1 和 0，不能顺序覆盖变量。

整合边界：B-E04 独立完成 update/v1 的语义绑定和计划快照；完整 UPDATE 需要 A-E01 的输入、D-E02 的执行器和 C-E07 的变长行支持，集中整合时必须对齐同一版本。
