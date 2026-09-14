# 成员 A 扩展 A-E01 至 A-E03

本次交付 UPDATE、ORDER BY/LIMIT、DISTINCT 的前端解析、JSON payload、错误诊断和测试。默认仍只接受基础 SQL；没有实现记录更新、排序执行或结果去重。

## 使用

```python
from minidb.frontend import Frontend

frontend = Frontend(enabled_extensions={"update", "order_limit", "distinct"})
statements = frontend.parse("""
UPDATE student SET age=21,name='李四' WHERE id=1;
SELECT id FROM student ORDER BY age DESC,id LIMIT 2;
SELECT DISTINCT name,age FROM student;
""")
for statement in statements:
    print(statement.feature, statement.version, statement.payload)
```

运行本机演示：

```powershell
cd D:\big\mini-db
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m demo.frontend_demo --file demo/extensions.sql --enable update --enable order_limit --enable distinct
```

`--enable` 可以重复传入。不开启对应功能时，首次遇到其关键字便抛 `SYNTAX/EXTENSION_DISABLED`，不会静默启用；未知配置名称报 `UNSUPPORTED/UNKNOWN_EXTENSION`。字符串值必须放集合中，例如 `{"update"}`，不能传 `"update"`。

启用扩展后，`Frontend.parse`/`Parser.parse` 的结果可能包含 `ExtensionStatement`；未使用扩展的语句仍返回原来的核心 Statement，字段和 Span 不变。冻结 `FrontendPort` 和 `ast.Statement` 的核心定义没有改动；整合层启用扩展前必须能显式分派扩展容器，不能直接送入只支持核心 Statement 的 B 编译器。

## 文法和输入边界

```ebnf
update      = UPDATE name SET assignment {"," assignment} [WHERE expr]
assignment  = name "=" expr
order_limit = select [ORDER BY order_term {"," order_term}] [LIMIT INTEGER]
order_term  = name [ASC | DESC]
distinct    = SELECT DISTINCT ("*" | names) FROM name [WHERE expr]
```

order_limit 只有真正出现 ORDER BY 或 LIMIT 才包装为扩展；无后缀时仍是核心 SELECT。expr、name、names 沿用核心文法。UPDATE 的 SET 赋值符只接受 `=`；WHERE 比较继续兼容 `==` 和 `<>`。一般算术属于 A-E07，`SET age=age+1` 当前会在 `+` 报语法错误。

| 任务 | feature/version | payload 键 | 保留规则 |
|---|---|---|---|
| A-E01 | update / 1 | table、assignments、where | assignments 为列表，每项 column/expr 均为 NodeJSON；保留重复赋值和原顺序，无 WHERE 为 null |
| A-E02 | order_limit / 1 | query、order_by、limit | query 是核心 SelectStmtJSON；排序项 column/direction，默认 ASC；limit 缺省 null，0 合法 |
| A-E03 | distinct / 1 | query | query 是没有 DISTINCT 字段的核心 SelectStmtJSON；星号 columns=null；显式投影重复项不删除 |

NodeJSON 固定 `{kind,fields,span}`。每个 Span 含 start/end，坐标仍对应原 SQL，而不是删除关键字或后缀后重新解析的字符串。排序的内层 query Span 只覆盖核心 SELECT 部分，外层 Span 覆盖整个后缀。DISTINCT 的内层 query 没有独立 DISTINCT 字段，其 Span 仍覆盖真实输入的 SELECT 语句。

LIMIT 只接受非负 INTEGER Token，不接受负号、正号、小数、字符串或标识符，不自行施加手册未规定的 INT32 上限。ORDER BY 在 LIMIT 之前，重复子句或逆序报 `INVALID_CLAUSE_ORDER`。列是否存在、是否可以排序、赋值类型是否匹配均交给 B。

DISTINCT 只能紧跟 SELECT 出现一次。`SELECT DISTINCT id FROM t ORDER BY id` 和带 LIMIT 的形式明确报 `UNSUPPORTED_COMBINATION`，因为本次三份 v1 任务没有定义组合 payload；不能丢失其中一项含义，也不私自嵌套另一种扩展协议。

## 快照和统一契约

三个 `frontend_*_v1.json` 是手册字段的对照样例，每份包含一个合法完整 payload 和至少两个非法 SQL。其期望值从显式字段和原字符串坐标构造，不由被测 Parser 生成。它们用于断言，不替代公共手册规范。DISTINCT/v1 是 A-E03 的前端局部约定，尚未承诺跨层执行。

本目录中的扩展快照来自成员 A 的独立交付，当前代码已将其前端实现适配到仓库统一冻结契约。`minidb/contracts/` 和根目录 `contracts.sha256` 才是整合时的唯一权威；`contracts.core-v1.sha256` 仅作为该独立交付的历史快照，不参与当前契约校验，也不能覆盖主分支的 Token、AST 或端口定义。扩展通过主分支已有的 frozen dataclass `ExtensionStatement(feature,version,payload,span)` 传递。

构造扩展信封时会验证 JSON 基本值并复制 payload；payload 的 dict/list 在复制后按只读约定使用。当前核心契约明确不接受 AST 实例、tuple、非字符串键等非 JSON 值。四份项目整合前必须统一采用根目录的 `contracts.sha256`，发现任一输入的契约目录不一致时拒绝导入。

为使扩展能够被观察和验证，本次还更新了前端 formatter、已有演示工具及交付生成器。没有实现或修改其他成员模块；本次合并只把前端实现、测试、演示和扩展快照纳入统一主分支。

## 验收和后续配合

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/frontend/test_a_e01.py tests/frontend/test_a_e02.py tests/frontend/test_a_e03.py
.\.venv\Scripts\python.exe tools/validate_frontend.py
```

完整校验会保留基础测试，验证前端和契约、运行核心及扩展演示，并测试不开扩展开关时的失败退出码。真实结果、每项测试名、源码哈希记录在 delivery 中，个人理解验收仍待本人完成。

UPDATE 执行需要 B-E04、C-E07、D-E02；排序/LIMIT 执行需要 B-E05、D-E03；DISTINCT 还需另定义 B 的去重计划和 D 的去重算子。当前三个扩展均仅标记“前端功能通过”。
