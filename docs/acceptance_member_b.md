# 成员 B SQL 编译器验收准备

## 启动工作台

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe run_gui.py
```

启动后切换到第五个标签页“05  B 编译器验收”。从下拉列表选择“词法分析（4分）”“语法分析（4分）”“语义分析（4分）”或“执行计划生成（4分）”。左侧会显示该项的真实测试文件、测试命令和代表性 SQL；选择动作会自动运行对应的 pytest，右侧随后显示真实标准输出、标准错误、退出码和结论。

测试运行期间请等待进度结束。失败时右侧会显示“失败”和实际 pytest 输出，不会把失败转换成通过。四个测试都使用当前仓库的测试代码，测试进程不操作工作台当前数据库。

进入“SQL 工作台”，点击“导入 SQL”，选择 `docs/acceptance_member_b.sql`，然后点击“执行 SQL”或按 F5。

执行后在结果下拉框中逐条选择语句，并在右侧查看：

```text
TOKEN
AST
SEMANTIC
BOUND
PLAN
OPTIMIZATION
OPTIMIZED_PLAN
EXECUTION
```

## 四项评分展示

### 词法分析 4 分

验收脚本包含关键字、标识符、整数、字符串、比较运算符、括号、逗号、分号和 SQL 注释。选中任意结果后查看 TOKEN，说明 Lexer 已将输入拆成带位置的 Token。

可额外执行下面的错误用例，展示非法字符和错误位置：

```sql
SELECT name FROM student WHERE age > ;
```

### 语法分析 4 分

验收脚本包含 CREATE、INSERT、SELECT、DELETE 四类核心语句。点击 AST，说明 Parser 将 Token 构造成对应的不可变 AST。

语法错误示例：

```sql
SELECT name FROM student WHERE age > ;
```

应看到语法阶段错误和源位置，并且错误脚本不会产生执行结果。

### 语义分析 4 分

先执行验收脚本创建表，再分别执行：

```sql
SELECT missing FROM student;
SELECT name FROM student WHERE age >= '18';
INSERT INTO student(id, name) VALUES (5, 'OnlyTwoValues');
```

分别展示缺列、类型不匹配和列数不匹配。重点说明 B 通过 Catalog 获取 `TableMeta`，把列名绑定为 `BoundColumn(index, name, dtype, span)`，并在生成计划前报告错误。

### 执行计划生成 4 分

选择第一条带 WHERE 的 SELECT，查看 PLAN：

```text
ProjectPlan(indices=(0, 1), names=('id', 'name'))
└── FilterPlan(age >= 18 AND NOT(name = 'Alice'))
    └── SeqScanPlan(student)
```

选择 DELETE，查看 PLAN：

```text
DeletePlan
└── FilterPlan(id = 2)
    └── SeqScanPlan(student)
```

说明 SELECT 的 Project 负责输出列，DELETE 不生成 Project，因为删除需要保留完整原始行和 RID，最后由 D 调用 C 的 `mark_delete`。

## 代码讲解位置

| 评分内容 | 代码位置 | 关键符号 |
|---|---|---|
| Catalog 元数据 | `minidb/compiler/catalog_service.py` | `CatalogService`、`get_table`、`register_table` |
| 语义绑定 | `minidb/compiler/semantic.py` | `resolve_column`、`bind_expression`、`bind_select`、`bind_delete` |
| 计划生成 | `minidb/compiler/planner.py` | `Compiler`、`build_plan` |
| 优化 | `minidb/compiler/optimizer.py` | `fold_constants`、`simplify_boolean`、`optimize` |
| 规则框架 | `minidb/compiler/rules.py` | `Rule`、`RuleOptimizer` |
| 接口契约 | `minidb/contracts/ports.py` | `CatalogPort`、`CompilerPort`、`StoragePort` |
| 工作台装配 | `minidb/integration/app.py` | `MiniDBApplication.execute` |

## 真实测试结果

已在 Python 3.14 环境中运行：

```text
\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --ignore=tests/compiler/test_b_e05.py tests/frontend tests/compiler tests/integration
246 passed in 4.04s

\.venv\Scripts\python.exe -m compileall -q minidb
compileall: passed
```

已提交版本排除本地未上传的 B-E05 后，全量测试结果：

```text
382 passed in 15.08s
```

## 验收时的边界说明

当前核心完整执行链是 `CREATE / INSERT / SELECT / DELETE`。UPDATE、ORDER BY/LIMIT、DISTINCT 当前主要用于前端解析展示；B-E01～B-E04 的扩展已完成对应编译器逻辑和测试，但不能把尚未接入执行器的扩展描述为完整端到端执行。
