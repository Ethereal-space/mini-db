# MiniDB：SQL 编译器、页式存储与执行引擎实验

本仓库用于《大型平台软件设计实习》的 MiniDB 小组实验。系统以一条可追踪的数据流为主线：

```text
SQL → Token → AST → Semantic → Bound AST → Logical Plan
    → Optimized Plan → Executor → Storage Engine → Buffer Pool → Page → Disk
```

项目使用 CPython 3.14.2（接受 `>=3.14,<3.15`），运行时代码只依赖标准库；开发测试固定 pip 25.3 与 pytest 9.1.1。源码、测试、提示词和数据文件统一采用 UTF-8，存储页固定为 4096 字节。

## 课程依据

方案共同依据以下三份材料，具体定位规则和追踪关系见 [references.md](docs/references.md)：

- `E:/SQL/《大型平台软件设计实习》指导书.pdf`，简称 `G-Pn`。
- `E:/SQL/大型平台软件设计实习_SQL编译器设计与实现_完整版.pptx`，简称 `C-Sn`。
- `E:/SQL/大型平台软件设计实习-2026-2.pptx`，简称 `D-Sn`。

## 四人独立边界

| 成员 | 负责目录 | 核心职责 |
| --- | --- | --- |
| A | `minidb/frontend/`、`tests/frontend/` | Lexer、Parser、AST 输出 |
| B | `minidb/compiler/`、`tests/compiler/` | Catalog 服务、语义、计划、优化 |
| C | `minidb/storage/`、`tests/storage/` | 4KB 页、缓冲池、表堆、Catalog 持久化 |
| D | `minidb/runtime/`、`tests/runtime/` | 执行算子、结果、Trace、CLI |
| 小组整合 | `minidb/integration/`、`tests/integration/` | 装配与端到端验收 |

`minidb/contracts/` 是冻结契约。成员模块只依赖契约，不直接导入其他成员的具体实现；独立测试使用符合契约的 Fake。共同规范见 [PROJECT_SPEC.md](docs/PROJECT_SPEC.md)，接口见 [INTERFACES.md](docs/INTERFACES.md)，文法见 [grammar.md](docs/grammar.md)。

## 让 AI 直接开始任务

仓库为每一项任务准备了可机器识别的 Markdown 任务卡。向仓库型 AI 发送一句话即可：

```text
我是成员 A，执行任务 A-B01。
```

“进行任务”“完成任务”以及可选空格也可识别。AI 应先读取 [AI_START_HERE.md](docs/ai/AI_START_HERE.md)，再从 [catalog.json](docs/tasks/catalog.json) 定位唯一任务卡。任务卡包含授权路径、原理、步骤、不变量、错误路径、测试断言、独立验收提示词、讲解题和整合条件。任务卡中的 `AI_TASK_BEGIN`、`TASK_META` 与 `AI_TASK_END` 是机器边界，不能删除。

24 项基础任务全部属于核心交付。扩展任务均为候选能力；只有 [feature-groups.json](docs/integration/feature-groups.json) 中被明确选择且组内条件完整的能力才进入集中整合，其他扩展保持独立。整合规则见 [INTEGRATION_PLAYBOOK.md](docs/integration/INTEGRATION_PLAYBOOK.md)。

## 环境与验证

```powershell
python --version
python -m pip --version
python -m pip install -r requirements-dev.txt
python -X utf8 -m compileall -q minidb
python -X utf8 -m pytest -q
```

期望输出为 Python 3.14.2、pip 25.3、pytest 9.1.1。CI 在 Python 3.14.2 的 Windows 与 Ubuntu 24.04 上执行相同核心检查。

核心演示：

```sql
CREATE TABLE student(id INT, name VARCHAR, age INT);
INSERT INTO student(id,name,age) VALUES (1,'Alice',20);
INSERT INTO student(id,name,age) VALUES (2,'Bob',17);
INSERT INTO student(id,name,age) VALUES (3,'Tom',22);
INSERT INTO student(id,name,age) VALUES (4,'张三',21);
SELECT id,name FROM student
WHERE 1 = 1 AND age >= 18 AND NOT (name = 'Tom');
DELETE FROM student WHERE id = 1;
SELECT * FROM student;
```

验收同时检查 Token/AST/语义/原计划/优化计划 Trace、跨页数据、LRU 与 FIFO、dirty 写回、tombstone、Catalog 页式恢复以及程序重启后的数据恢复。详细边界与可重复断言均在任务卡中。

## 最终整合

将四份完整项目副本放入：

```text
_integration_input/member_a/
_integration_input/member_b/
_integration_input/member_c/
_integration_input/member_d/
```

向最终整合 AI 声明已经完成的扩展任务，它会按 [feature-groups.json](docs/integration/feature-groups.json) 选择完整功能组，校验契约与 24 项基础任务，并把结果、`feature_selection.json` 和 `integration-report.md` 写入 `_integration_output/mini-db/`。未声明扩展不会进入结果，部分扩展组会被跳过且不影响核心。

仓库只保存四人共同基线。项目不规定成员的提交、分支、同步或推送方式；成员 AI 与最终整合 AI 均不执行 Git 提交或推送。

## 文档导航

- [AI 开工入口](docs/ai/AI_START_HERE.md)
- [项目规格](docs/PROJECT_SPEC.md)
- [冻结接口](docs/INTERFACES.md)
- [核心 SQL 文法](docs/grammar.md)
- [课程材料依据](docs/references.md)
- [68 项任务目录](docs/tasks/catalog.json)
- [集中整合手册](docs/integration/INTEGRATION_PLAYBOOK.md)
- [完整 Word 开发实施手册](docs/manual/MiniDB软件设计实验四人VibeCoding开发实施手册.docx)
- [开发实施手册清单](docs/manual/manual-manifest.json)
