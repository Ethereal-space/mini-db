# MiniDB 成员 A SQL 前端

已实现实施手册的 A-B01 至 A-B06，以及 A-E01（UPDATE）、A-E02（ORDER BY/LIMIT）、A-E03（DISTINCT）前端扩展。提供词法分析、递归下降解析、WHERE 布尔表达式、精确错误位置、多语句和 Token/AST 输出。当前成果只负责 SQL 到 AST，不执行查询、不写数据库；B/C/D 模块留给对应成员。

## 本机运行

项目目录：`D:\big\mini-db`。本机虚拟环境已配置 Python 3.14.2、pip 25.3、pytest 9.1.1。核心运行代码仅使用标准库。

```powershell
cd D:\big\mini-db
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools/validate_frontend.py
.\.venv\Scripts\python.exe -m demo.frontend_demo --file demo/core.sql
.\.venv\Scripts\python.exe -m demo.frontend_demo --sql "SELECT id FROM student WHERE NOT age=18"
```

`demo/core.sql` 应产生 8 棵 AST；它只展示建表/插入/查询/删除的语法结构，不能据此声称数据库操作完成。`demo/errors.sql` 展示缺少比较右操作数的 SYNTAX 错误，演示退出码为 1。不传参数时从 stdin 读取到 EOF。

桌面图形演示会保持窗口运行，可以输入 SQL、选择拓展开关，并分别查看 Token、AST 和错误信息：

```powershell
.\.venv\Scripts\python.exe -m demo.frontend_gui
```

在 PyCharm 中也可以直接运行 `demo/frontend_gui.py`。按 F5 或 Ctrl+Enter 解析 SQL；“基础示例”和“拓展示例”按钮可以快速载入样例。这个窗口用于展示成员 A 的解析结果，不执行数据库查询。

扩展必须显式开启，详细协议和限制见 `docs/extensions/README.md`：

```powershell
.\.venv\Scripts\python.exe -m demo.frontend_demo --file demo/extensions.sql --enable update --enable order_limit --enable distinct
```

该文件分别产生 update、order_limit、distinct 三个 ExtensionStatement。三种扩展可在同一脚本的不同语句中使用；DISTINCT 与排序/LIMIT 在同一条语句中组合尚未定义，明确拒绝。

## 换电脑

复制项目源码，不复制 `.venv`。安装 CPython 3.14.2 后，在项目根目录执行：

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install pip==25.3 pytest==9.1.1
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m pytest -q
```

Linux 使用 `python3.14 -m venv .venv` 和 `.venv/bin/python`。本次只在 Windows 实测，不把 Linux 兼容性推断为已验收。

## 文件导航

- `minidb/frontend/lexer.py`：A-B01/B02，游标、Token、字面量、注释。
- `minidb/frontend/parser.py`：A-B03 至 B06，语句、表达式、Frontend 端口。
- `minidb/frontend/formatter.py`：A-B06，稳定 Token/AST JSON。
- `demo/frontend_gui.py`：持续运行的桌面图形演示，提供 SQL 输入和 Token/AST/错误输出。
- `tests/frontend/test_a_b01.py` 至 `test_a_b06.py`：逐任务断言及边界回归。
- `tests/frontend/test_a_e01.py` 至 `test_a_e03.py`：扩展 payload、开关、源码位置和核心兼容回归。
- `docs/extensions/README.md` 与三个 `frontend_*_v1.json`：扩展接口、示例和整合依赖。
- `docs/grammar.md`：核心文法、位置、语义边界和资源限制。
- `docs/INTERFACES.md`：交给成员 B 和整合层的接口。
- `docs/walkthrough/member_a.md`：真实代码讲解与理解练习。
- `delivery/member_a.json`、`delivery/test-results.xml`、`delivery/validation.txt`：交付清单和运行证据。

## 基线说明

开始时目录没有手册要求的共同基线。本次为完成 A 的基础部分，在 `minidb/contracts` 初始化了必要的 Token、AST、Span、错误、TraceEvent 和 FrontendPort；这是**前端最小基线**，不是完整四人项目初始化。没有生成 68 项全组任务框架或 B/C/D 代码。`contracts.sha256` 固定本次快照，整合前应由四人统一采用或显式对照现有团队基线。

本次初始化公共文件和演示脚本是补齐缺失前置；后续成员 A 开发遵循手册范围，只改前端、对应测试、个人 walkthrough 和 delivery。没有执行 Git 提交或推送。

A-E01 至 A-E03 阶段另补齐了手册规定但最小基线缺失的 `ExtensionStatement` 容器。原 7 个核心契约文件保持不变，旧清单保存在 `docs/extensions/contracts.core-v1.sha256`；最新清单新增 `extensions.py`，整合前需统一。详情见扩展说明。

## 验收状态

功能与理解分开记录，原有 111 项基础测试继续保留，另有扩展测试；最新总数和实际命令结果见 delivery。运行 `tools/validate_frontend.py` 会重新验证版本、测试、演示并更新交付证据。个人口述、Debug 和现场修改需要你本人完成，当前标为“待完成”，不会用 AI 的解释代替本人验收。括号嵌套的实现上限为 64 层，超过后明确报错；AND/OR/NOT 长链使用循环及显式栈处理。
