# Member B Acceptance Panel Design

## Goal

在 MiniDB Studio 中增加一个面向成员 B 现场验收的专用标签页。验收人员从下拉列表选择“词法分析、语法分析、语义分析、执行计划生成”四项之一后，左侧显示该项真实测试代码，右侧自动运行对应测试并显示实时结果、命令和结论。

## Scope

本次只增加工作台的展示与测试调度能力，不修改冻结 contracts、编译器核心语义、存储层、运行时执行器或其他成员模块。允许修改的范围为：

- `minidb/integration/workbench_gui.py`
- `minidb/integration/workbench_model.py`
- `tests/integration/test_workbench_gui.py`
- `tests/integration/test_workbench.py`
- `docs/acceptance_member_b.sql`
- `docs/acceptance_member_b.md`

如果测试面板需要固定的测试描述，将其作为 integration 层的不可变配置保存，不把测试结果写成生产代码中的假数据。

## User Flow

1. 用户运行 `run_gui.py`。
2. 用户打开“B 编译器验收”标签页。
3. 下拉框显示四个评分项和分值。
4. 用户选择一个评分项。
5. 左侧代码区更新为该项对应的真实 SQL 或测试命令/代码说明。
6. 面板自动在独立临时环境中运行对应测试。
7. 右侧结果区显示真实 stdout、stderr、退出码、通过/失败数量和一段面向答辩的验证结论。
8. 运行期间界面显示忙碌状态，避免重复提交；完成后恢复可选状态。

## Test Cases

四项展示内容对应当前已经存在的真实测试：

| 面板选项 | 主要代码/SQL | 验证重点 |
|---|---|---|
| 词法分析（4分） | `tests/frontend/test_a_b01.py`、`test_a_b02.py` | 关键字、标识符、常量、运算符、非法输入和 Span |
| 语法分析（4分） | `tests/frontend/test_a_b03.py`～`test_a_b06.py` | CREATE、INSERT、SELECT、DELETE AST 与典型语法错误 |
| 语义分析（4分） | `tests/compiler/test_b_b01.py`～`test_b_b04.py` | Catalog、列绑定、类型检查、列数匹配和错误位置 |
| 执行计划生成（4分） | `tests/compiler/test_b_b05.py`、`test_b_b06.py` | Project、Filter、SeqScan、DeletePlan 和优化后计划 |

测试运行必须调用真实 pytest，不在 GUI 中硬编码“通过”。测试代码区显示可复现的测试路径和代表性 SQL，结果区显示该次进程的实际输出。

## Data Flow

```text
下拉选项
  → integration 验收配置
  → pytest 子进程
  → 后台线程
  → UI 队列
  → 代码区和结果区
```

面板复用工作台已有的后台线程和队列机制。pytest 子进程使用仓库当前 Python 环境，通过 `sys.executable` 或工作台已确认的虚拟环境启动，并以仓库根目录为 cwd。测试不会调用用户当前数据库，也不会修改当前工作库。

## Error Handling

- 找不到测试文件：结果区显示明确路径和错误，不显示成功。
- pytest 返回非零：保留完整 stdout、stderr 和退出码，并显示失败状态。
- 子进程启动异常：显示异常类型和原因。
- 用户在运行中重复选择：更新选项可以延迟到当前任务完成，不能并发写同一个结果区。
- 测试超时：终止该测试进程并显示超时状态；不能无限阻塞 Tk 主线程。

## Invariants

- Tk 控件只在主线程更新，pytest 运行在后台线程。
- 每次选择只对应一个当前结果，旧结果不会冒充新结果。
- 结果内容来自真实测试进程，不创建假通过文本。
- 现有 SQL 工作台、前端分析、页与缓存、使用指南功能保持不变。
- 四个评分项的顺序和名称稳定，选择结果可重复。
- 测试使用独立临时目录或只读测试目标，不污染用户数据库。

## Verification

新增测试至少覆盖：

- 四个下拉选项完整存在且分值正确。
- 选择每个选项后代码区显示对应测试路径或 SQL。
- 选择动作触发测试任务，结果区显示真实退出码和输出。
- 测试失败时显示失败状态而不是成功结论。
- 测试运行期间不会重复启动任务。
- 原有工作台集成测试继续通过。

验证命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/integration/test_workbench.py tests/integration/test_workbench_gui.py
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m compileall -q minidb
```
