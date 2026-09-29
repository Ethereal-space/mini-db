# 成员 A 基础实现讲解

本文对应 A-B01 至 A-B06 及文末 A-E01 至 A-E03。源码文件和测试快照哈希在 `delivery/member_a.json`，实际命令与结果在 `delivery/validation.txt`。这是代码讲解材料，不能代替本人理解验收。

## 总体数据流与文件边界

`Frontend.parse(source)` 创建 `Lexer(source)`，生成带 Span 的 Token；再创建 `Parser(tokens)`，按完整脚本返回 Statement 列表。词法成功可产生 TOKEN 事件，全部语法成功后才产生 AST 事件。事件只包含格式化文本，观察者无法拿事件修改原 AST。

Lexer 保存字符游标；Parser 保存 Token 下标。它们都不访问 Catalog，也不调用存储。Frontend 每次使用全新的游标对象，所以一次失败不会污染下次调用。四类语句的表名、列名均用 Identifier 保存原位置；列和值的顺序交给 B 解释。

## A-B01 字符游标与标识符

`Lexer.__init__` 保存原 SQL 和 offset=0、line=1、column=1。非字符串输入是调用方编程错误，抛 TypeError。

`peek` 查看当前或后续字符，末尾返回空串，不修改状态。`position` 创建不可变 Position 快照。`advance` 是唯一推进坐标的方法：普通码点（包括 Tab）增加一列，LF 或单独 CR 换行，CRLF 同时消费两个码点但只换一行。返回值为原文切片，因此字符串中的 CRLF 不会被改成 LF。

`scan_identifier` 消费 ASCII 字母、数字和下划线，先完整取 lexeme，再检查 64 字符上限，最后查 KEYWORDS。关键字和标识符 value 规范成小写，但 lexeme 保留大小写。`select_x` 不会被拆成 SELECT 与后缀。

`_token` 以扫描前 Position 和当前 Position 构造半开 Span。`tokenize` 循环只选择一种扫描分支，每轮必然前进或抛错；最后添加零宽 EOF。`_error` 为词法失败固定 LEXICAL 阶段，并覆盖真实消费范围。

例：`SELECT\r\n\tname` 中 SELECT 占 offset 0..6；CRLF 后 offset=8、line=2、column=1；Tab 后 offset=9、column=2。因此 name 起点 `(9,2,2)`，终点和 EOF 均为 `(13,2,6)`。

验证：`test_a_b01_keyword_identifier`、`test_a_b01_positions`、`test_a_b01_identifier_boundary`、`test_a_b01_identifier_limit`。错误路径 `SELECT @` 由 `test_a_b01_illegal_character` 精确断言 `[7,8)`。

## A-B02 数字 字符串与注释

`scan_number` 先读整数部分，再检查可选的小数点及至少一位小数。紧接字母、下划线或额外点号时，把非法片段作为数字错误，而不是拆开蒙混通过。`_decimal_integer` 按 9 位分块构造整数，避免超长输入触发 Python 的十进制转换限制；INT32 范围不在这里判断。FLOAT 必须可表示为有限数，否则给出定位错误。

`scan_string` 记录开引号位置；普通字符用 advance 保留原值；遇单引号时，如果下一字符仍是单引号，就向值追加一个单引号；否则结束字符串。EOF 前没有闭引号时错误指向整个未闭合结构。字符串中的 `--` 或 `/*` 只是内容。

`scan_operator_or_comment` 先判断两类注释，再尝试双字符符号，最后单字符。块注释到第一个 `*/` 结束，不嵌套；注释内的单引号不进入字符串状态。冻结契约没有单独的别名种别，因此 `==` 的 lexeme 仍保留两个等号、kind 使用 `EQ`、value 规范为 `=`；Parser 通过同一比较映射保持语义一致，`<>` 同理使用 `NE`。

验证：`test_a_b02_number_and_operator_tokens`、`test_a_b02_string_escape`、`test_a_b02_comment_positions`、`test_a_b02_multiline_string_and_comment_states`。`test_a_b02_bad_number` 覆盖非法数字；`test_a_b02_unterminated` 覆盖两类未闭合结构。

`-2147483648` 在 Lexer 中是 MINUS 和 INTEGER(2147483648)，不能在此拒绝正数部分；Parser 合并后才是合法 INT32 最小值，由 B 最终检查。

## A-B03 CREATE 与 INSERT

`Parser.__init__` 检查末尾唯一 EOF 并拷贝 Token 序列为 tuple。`peek` 取当前 Token，`consume` 在非 EOF 时增加下标，`expect` 检查允许种别并返回 Token，否则调用 `_unexpected`。

`_unexpected` 统一构造 actual、lexeme、expected 和当前 Span，错误展示包含原因。缺列表分隔符时，`_comma_list` 同时报告 COMMA 与结束符，不给笼统错误。列表至少一项，末尾逗号后仍必须有合法项。

`parse_column_def` 构造名称节点和 INT/VARCHAR 类型名。`parse_create_table` 依次消费关键字、表名、括号、列定义；返回的 Span 从 CREATE 开头到右括号末尾，不包含分号。重复列原样保存。

`parse_literal` 只接受整数、字符串、小数或 MINUS INTEGER。负整数字面量的 Span 包含负号；不接受负浮点或一般算术。`parse_insert` 区分 columns=None 与显式 tuple，值不重排、不转换、不检查真实表。

验证：`test_a_b03_create_structure`、`test_a_b03_insert_column_order`、`test_a_b03_literal_boundary`、`test_a_b03_missing_separator`、`test_a_b03_semantic_boundary`。`test_a_b03_does_not_check_schema_or_value_limits` 证明超界值和重复名称仍交给 B。

## A-B04 SELECT 与 DELETE

`parse_projection` 将单独星号表示为 None，否则返回保留顺序和重复项的 Identifier tuple。`SELECT *,id` 在逗号报错。`parse_select` 按 SELECT、投影、FROM、表名、可选 WHERE 顺序生成 SelectStmt。它不生成 ProjectPlan。

`parse_delete` 要求 DELETE FROM；没有 WHERE 就保留 where=None，这是合法的全表删除语法描述。`parse_where_optional` 是两种语句共用的表达式入口。它们都用 `_span_since` 从首 Token 到最后消费 Token 计算语句位置。

验证：`test_a_b04_select_star`、`test_a_b04_projection_order`、`test_a_b04_delete_optional_where`、`test_a_b04_missing_from`。`test_a_b04_reject_extra_syntax` 检查 ORDER、LIMIT、别名、JOIN、投影算术和缺关键字，不能忽略尾随 Token。

## A-B05 表达式调用链

调用层次为 `parse_expression → parse_or → parse_and → parse_not → parse_comparison → parse_primary`，与公共 grammar.md 一一对应。

OR 和 AND 都先读一个更紧结合的子表达式，再循环把新右侧接到已有左树，所以同级左结合。NOT 使用显式列表模拟右递归：先收集前缀，解析完整 comparison，再逆序包裹 UnaryExpr，能处理很长的 NOT 链。

`parse_comparison` 只允许一个比较符；如果右侧 primary 后又出现比较符，立即在第二个符号处抛 CHAINED_COMPARISON。兼容运算符由 COMPARISONS 映射到标准 op。`parse_primary` 处理名称、字面量或括号；括号递归到完整 expression，关闭后用 dataclasses.replace 扩展根节点 Span，不修改原子节点。

```text
NOT age=18 AND id=1
AND
  NOT
    =(age,18)
  =(id,1)

a=1 OR b=2 AND c=3
OR
  =(a,1)
  AND
    =(b,2)
    =(c,3)
```

真实错误例：`SELECT * FROM t WHERE a<1<2;` 在第二个 `<` 的第 26 列失败；`SELECT * FROM t WHERE a=1 AND;` 在分号的第 30 列失败。两处均由独立 source 索引和 Position 断言交叉验证，手册位置正确。首轮测试中两个手写数字误填为 25、28，修正了测试，未修改生产定位算法。

验证：`test_a_b05_and_or_tree`、`test_a_b05_not_comparison`、`test_a_b05_parentheses_and_alias`、`test_a_b05_reject_comparison_chain`、`test_a_b05_missing_operand`。另外覆盖全部八种输入比较符、左结合、括号边界和类型不应在 A 检查的情形。

## A-B06 完整脚本与格式化

`Parser.parse` 循环跳过空分号、解析完整语句、验证其后为分号或 EOF。仅到全部成功才返回列表，第二条出错不会返回第一条。`parse_statement` 根据首 Token 分派四个方法。

`Frontend.parse` 先完成全量 tokenize，再发 TOKEN 事件；语法全部成功后发 AST 事件。观察回调为 None 时不做格式化，保持同样的解析和错误行为；观察者本身的异常不包装为用户错误。

`ast_to_data` 用显式待处理栈把 AST 变成 JSON 基本值，字段次序按 dataclass 定义固定，tuple 转为列表。`_span_data` 保留完整位置。`format_ast` 和 `format_tokens` 用 `_json_text` 输出稳定 JSON。容器不递归，只有标量使用 json.dumps，因此很长的 AND/NOT 树也可以输出 Trace。`_integer_text` 对超长整数分块输出，避免在展示阶段再次触发 Python 数字转换限制。

验证：`test_a_b06_multiple_statements`、`test_a_b06_stable_formatter`、`test_a_b06_formatter_snapshot`、`test_a_b06_error_and_trace`、`test_a_b06_failure_never_reports_partial_ast`。`test_a_b06_long_expression_and_trace_without_recursion_error` 覆盖 1500 项 AND 链和 1500 项 NOT 链的结构及事件输出。`test_a_b06_huge_integer_formatter` 验证 5001 位整数的解析和输出。

## 不变量 异常与资源限制

1. 源码从不预先统一换行，否则 offset 不能切回原文。
2. 前端只消费输入并构造新对象，不访问表目录或磁盘。
3. tuple 保留语法顺序；重复名称不去重；SELECT 星号不伪造成名为 `*` 的列。
4. 不用 eval，不捕获任意异常后伪装成语法错误。
5. 括号最多嵌套 64 层，超过后抛真实位置的 NESTING_LIMIT。有限 float 边界也显式报错。它们是实现资源边界，已在 grammar.md 说明。
6. Token/AST Trace 为整批事件，只发一次。把事件附到哪个 ExecutionResult 属于最终整合职责。

## 独立验收与本人练习

契约测试固定字段、不可变性、导入边界及哈希；前端测试按任务编号分文件。测试均包含实际字段、结构或错误位置断言，没有 skip、xfail 或“只要不崩溃就通过”的占位测试。完整命令由 `tools/validate_frontend.py` 执行并保存 stdout、stderr、退出码。

本人理解状态：待完成。建议在阅读代码后依次练习：

- 手算 CRLF、Tab 和中文字符串后的坐标，解释 offset 为什么不是 UTF-8 字节数。
- 从 `NOT a=1 OR b=2 AND c=3` 画 AST 并逐层解释调用返回值。
- 解释显式 INSERT 列顺序为何不能由 Parser 排序。
- 找到 `SELECT id FROM;` 的 expect 失败点，预测 context.expected。
- 在练习副本中临时移除 `<=` 映射，先预测失败测试，运行观察后恢复。
- 解释整个脚本先 parse，为什么仍可以由整合层逐句建表再插入。

理解验收由本人回答和修改证明；本文件不记录未经实际进行的“本人通过”。

## A-E01 UPDATE 解析

`Frontend` 和 `Parser` 的构造参数 `enabled_extensions` 是独立的 keyword-only 参数，不改变原来的 on_trace 用法。`_extension_set` 校验名称并拷贝为 frozenset，调用方修改原 set 不会影响运行实例。Lexer 直接使用冻结契约的 `ALL_KEYWORDS` 生成 UPDATE/SET 等 Token；`_at_word` 按关键字种别判断，不会把字符串 `'UPDATE'` 当成语句。

`parse_statement` 在遇到 UPDATE 时先调用 `_require_extension`，未开启就抛明确语法错误。`parse_update` 消费 UPDATE、表名、SET，复用 `_comma_list` 读取至少一项 assignment，再读取可选 WHERE。`parse_assignment` 在列名之后严格消费赋值符 `=`，再调用原 `parse_expression`。每项返回 `{column,expr}`，其值通过 `ast_to_data` 转为 NodeJSON。

例如 `UPDATE student SET age=21,name='李四' WHERE id=1` 的 assignments 长度为 2，顺序 age、name；where 保留 `BinaryExpr('=',Identifier(id),Literal(1))` 的编码。`UPDATE t SET id=;` 在分号处报缺操作数；`SET id=1,id=2` 保留两次赋值让 B 诊断，不能转 dict 丢掉重复证据。

`ExtensionStatement` 是跨层使用的 JSON 信封，只负责 feature/version/payload/span 和契约规定的 JSON 值校验；前端在构造前已经用 `ast_to_data` 把 AST 转成普通字典、列表和标量。它不做 UPDATE 语义检查，也不执行写入。

关键测试：`test_a_e01_update_payload` 逐项验证结构和 JSON 往返，`test_a_e01_missing_value` 校验精确位置，`test_a_e01_preserve_duplicates_and_semantic_errors` 检查职责边界，`test_a_e01_payload_copy_and_json_only` 验证拷贝与非法对象，`test_a_e01_long_expression_json_copy_and_format` 使用 200 项布尔链检查格式化过程不依赖对象 repr。

本人练习：解释为何 assignments 是列表；从 `UPDATE t SET a=b,b=a` 说明 A 保留了什么、为何赋值求值方式必须由 D 决定。本人口述及修改状态仍为待完成。

## A-E02 ORDER BY 与 LIMIT

`parse_select` 先按原逻辑构造核心 SelectStmt，因此它的投影和 WHERE 保持原节点和原坐标。发现 ORDER 或 LIMIT 后，`parse_query_suffix` 才在开关允许时读取后缀并包装 order_limit/v1。

`parse_order_terms` 每次先读一个列名，再读取可选 ASC/DESC，默认 ASC，按出现次序追加列表。它不判断排序列是否存在，也不把排序列硬塞进输出投影。`parse_limit` 只接受 INTEGER，0 原样保存，缺省 None；负号、小数等在当前 Token 上报 INVALID_LIMIT。

子句只按 ORDER BY 再 LIMIT 消费。若随后又出现 ORDER/LIMIT，立即报 INVALID_CLAUSE_ORDER，其他多余 Token 仍由批次入口拒绝。这样 `LIMIT 2 ORDER BY id` 不会生成看似成功但漏掉排序的 AST。

`query` 字段的 Span 只覆盖原核心 SELECT，外层 ExtensionStatement 覆盖后缀。`SELECT * FROM t ORDER BY id` 和显式追加 ASC 的 payload 相等，因为排序语义和内部位置相同；外层语句 Span 则因额外文本而不同。

关键测试：`test_a_e02_order_limit` 验证 DESC/默认 ASC、limit 和投影；`test_a_e02_limit_zero` 防止 `0 or None` 类错误；`test_a_e02_bad_limit` 覆盖负号和 FLOAT；`test_a_e02_clause_order` 检查逆序和重复；`test_a_e02_default_and_explicit_asc` 对比默认方向；`test_a_e02_disabled` 证明基础模式仍拒绝扩展。

本人练习：说明为何 query 必须编码为 JSON，而不能放 SelectStmt 实例；解释为什么 A 不能根据 LIMIT 在解析阶段裁剪数据。本人口述及修改状态仍为待完成。

## A-E03 DISTINCT

`parse_select` 只在 SELECT 后检查一次 DISTINCT。开启后消费该标记，复用原投影、FROM、WHERE 解析，并在最终返回时包装 `{query: SelectStmtJSON}`。没有 DISTINCT 的 SELECT 仍返回普通 SelectStmt，不因为开关开启就改变全部 AST 类型。

第二次 DISTINCT 或缺失投影时，期望集合明确包含 IDENTIFIER、STAR。列列表不去重，`SELECT DISTINCT name,name` 保留两列，因为真正的 DISTINCT 是 D 对输出整行元组去重，不是 A 删除相同列名。字符串字面量 `'DISTINCT'` 始终是 STRING。

本次没有 DISTINCT 与 order_limit 组合契约，因此 `SELECT DISTINCT id FROM t LIMIT 2` 在 LIMIT 处抛 UNSUPPORTED_COMBINATION。三种功能可在同一脚本的不同语句中使用，这与单条 SQL 的扩展组合是两回事。

`formatter.ast_to_data` 增加对 ExtensionStatement 和 Mapping 的处理，保持相同 `{kind,fields,span}` 外层格式，并把 payload 转成 JSON 数据。原有核心格式未改变。`demo.frontend_demo` 的 `--enable` 可重复，`tools/validate_frontend.py` 验证开启/关闭扩展的真实进程退出结果，并由 `record_delivery` 更新三项任务和依赖记录。

关键测试：`test_a_e03_distinct_projection`、`test_a_e03_distinct_star`、`test_a_e03_double_distinct`、`test_a_e03_core_unchanged` 对应四个任务卡用例；`test_a_e03_duplicate_projection_not_deduplicated` 保留语法证据；`test_a_e03_mixed_batch_trace` 验证混合批次只发一次 TOKEN/AST；`test_a_e03_demo_real_process_and_default_disabled` 验证可运行演示。

本人练习：用 `(a,b)=(1,2),(1,3)` 说明整元组去重与分别对 a/b 去重的区别，再追踪有 WHERE 的 DISTINCT payload。本人理解状态待完成。
