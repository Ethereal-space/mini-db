# MiniDB 最终整合 AI 操作手册

本手册只供最终整合 AI 使用。普通成员任务不得修改 integration。仓库当前 `main` 已提供一份通过核心端到端测试的参考装配（`minidb/integration/app.py` 与 `minidb/__main__.py`）；当收到四份成员副本时，整合 AI 仍须按本手册重新校验并按输入证据生成输出，不能把现有入口或用户声明当作测试证据。整合对象是四份已经完成独立验收的完整项目副本，整合依据是用户声明的完成任务清单、冻结契约哈希、delivery 证据和 `feature-groups.json`，不依据 Git 分支或提交历史。

## 1. 用户输入

推荐输入：

```text
你是最终整合 AI。
基础任务全部完成。
已完成扩展任务：A-E01、B-E04、C-E07、D-E02。
四份代码位于 _integration_input/member_a 至 member_d。
按手册完成整合、测试和整合报告。
```

“已完成扩展任务”是允许清单。没有列出的扩展即使出现在成员副本中也必须忽略。用户说“基础任务全部完成”仍要用文件、delivery 和测试证据验证，不能直接视为通过。

## 2. 固定目录

```text
_integration_input/
  member_a/
  member_b/
  member_c/
  member_d/
_integration_output/
  mini-db/
```

每个成员目录是共同基线的完整副本，并包含本人模块、测试、walkthrough 和 `delivery/member_<x>.json`。输入、输出目录均不进入项目基线版本控制。

成员所有权：

| 输入 | 可导入生产模块 | 可导入测试与证据 |
| --- | --- | --- |
| member_a | `minidb/frontend/` | `tests/frontend/`、A walkthrough、A delivery |
| member_b | `minidb/compiler/` | `tests/compiler/`、B walkthrough、B delivery |
| member_c | `minidb/storage/` | `tests/storage/`、C walkthrough、C delivery |
| member_d | `minidb/runtime/` | `tests/runtime/`、D walkthrough、D delivery |

任何成员副本中的 contracts、其他成员模块、integration、公共测试和公共文档都不是该成员的可选版本。它们只能与共同基线逐字节一致。

## 3. 发布前硬门槛

1. 四个输入目录、四份本人 delivery JSON 和四份 walkthrough 均存在且 UTF-8 可读。
2. 共同基线、每份成员输入的 `minidb/contracts/` 以及记录的 `contract_hash` 完全一致。任何差异都拒绝对应输入，不允许多数表决或挑选“最新”契约。
3. `docs/tasks/catalog.json` 恰有 68 个唯一任务，其中 24 个 base；每个 base 在正确成员的 `completed_tasks` 中，并能找到授权生产文件与任务测试。
4. 输入不含跨成员具体实现导入、生产对 tests/Fake 的依赖、禁用库、占位实现、skip/xfail 或测试专用生产分支。
5. Python、pip、pytest 与 UTF-8 环境符合项目规格。

任一基础任务缺失或基础模块测试失败时，停止生成可发布结果，输出缺失任务编号、成员、文件/证据和失败命令。扩展缺失不能使已经通过的核心失败。

## 4. 固定整合算法

1. 解析用户声明，得到 `declared_extensions` 集合；拒绝未知或格式错误的任务编号。
2. 校验四份契约目录、`contracts.sha256` 和 delivery 中的 `contract_hash`；不一致的成员输入拒绝导入。
3. 按 catalog 验证 24 项基础任务及测试证据；生成准确的 `missing_base_tasks`。非空时停止发布。
4. 从干净共同基线建立 `_integration_output/mini-db/`，只复制 A 的 frontend、B 的 compiler、C 的 storage、D 的 runtime 及各自测试/证据；装配 `minidb/integration/app.py`、`adapters.py`、`minidb/__main__.py` 与 integration tests。
5. 如果实现只存在容器类型、迭代器或上下文管理的包装差异，仅在 `minidb/integration/adapters.py` 做显式、带测试的适配。不得改变名称、类型、状态语义、错误阶段或页格式。
6. 读取 `feature-groups.json`。只考察 `declared_extensions`：atomic 组必须全部声明且都有实现/测试证据；per_task 可逐项；retain_only 只保留代码证据；experimental 默认只产出实验说明。
7. 每导入一个核心模块立即运行该模块和 contracts 测试；每尝试一个扩展组立即运行组测试和受影响的核心回归。扩展失败时移除该组在输出中的正式注册和选择记录，保留核心及先前通过组。
8. 运行端到端场景，生成 `_integration_output/mini-db/feature_selection.json` 与 `integration-report.md`。报告必须来自实际输入、命令和结果，不得用用户声明代替证据。
9. 完成输出留在 `_integration_output/mini-db/`。不执行 Git commit、push、merge、tag 或发布。

## 5. 扩展选择

`docs/integration/feature-groups.json` 是机器可读真值。核心原子组如下：

| 功能 | 必需任务 | 结果 |
| --- | --- | --- |
| UPDATE | A-E01、B-E04、C-E07、D-E02 | 全部通过才注册 UPDATE |
| ORDER BY/LIMIT | A-E02、B-E05、D-E03 | 全部通过才注册排序/限制 |
| JOIN | A-E04、B-E06、D-E05 | 全部通过才注册 JOIN |
| GROUP BY/聚合 | A-E05、B-E07、D-E04 | 全部通过才注册聚合 |
| EXPLAIN | B-E09、D-E01 | 全部通过才开放完整输出 |

低耦合扩展逐项整合；缺少完整执行链的 DISTINCT、types、arithmetic 只保留已声明且通过的代码与测试，不注册为可执行 SQL；存储格式和系统实验使用隔离数据文件/格式版本，只生成实验说明，不改变核心 `mini.db`。

对每个跳过组都要计算：

```text
missing = required_tasks - declared_extensions
invalid = declared_extensions 中缺文件、缺测试、契约变化或测试失败的任务
```

报告分别列出 missing 与 invalid 的准确编号及原因。不得写笼统的“依赖不完整”。

## 6. 复制与回退纪律

- 先复制基础模块，再按组复制明确声明的扩展文件。不要整目录覆盖后再试图删除未声明扩展。
- 同一文件同时承载基础和多个扩展时，以基础通过版本为起点，按声明组应用可审计的最小差异；无法可靠分离时跳过该扩展并报告原因，不能把未声明代码带入正式入口。
- 测试文件也按 catalog 的任务归属选择。未声明扩展测试可留在输入副本，但不复制到正式输出。
- 一个扩展组失败后，只回退该组的代码、适配器注册、入口开关和测试选择；重新运行核心及已启用组回归。
- 禁止通过改 contracts、删除失败测试、添加 skip/xfail、弱化断言或硬编码演示数据使整合变绿。

## 7. 必跑验证

基础命令：

```powershell
python --version
python -m pip --version
python -m pytest --version
python -m compileall -q minidb
python tools/generate_contract_hash.py --check
python tools/check_task_catalog.py
python tools/check_import_boundaries.py
python tools/check_utf8.py
python tools/validate_integration_inputs.py --input-root _integration_input --task A-E01 --task B-E04 --task C-E07 --task D-E02 --output _integration_output/preflight.json
python -m pytest -q tests/contracts
python -m pytest -q tests/frontend tests/compiler tests/storage tests/runtime
python -m pytest -q tests/integration
python -m pytest -q
```

把示例中的四个 `--task` 替换为用户实际声明的扩展；没有声明扩展时省略这些参数。预检非零退出码表示基础任务、输入、契约或声明尚不能发布，必须先阅读其 JSON 报告，不能跳过预检继续装配。

至少验证八类整合场景：

1. 只声明并完成全部基础任务，核心全链路通过；
2. 完整声明 UPDATE 四项，UPDATE 启用并通过；
3. UPDATE 只声明部分任务，整组跳过且核心继续通过；
4. 输入副本含未声明扩展，其代码不进入正式注册和选择结果；
5. 某成员修改冻结契约，该输入被拒绝并列出差异；
6. 某扩展组测试失败，只回退该组；
7. 固定 SQL 演示、跨页写入、LRU/FIFO、DELETE、页式 Catalog 与关闭后重启恢复通过；
8. 新 AI 会话只收到成员与任务编号时，能从 catalog 找到唯一卡并开始执行。

固定 SQL 演示至少包含建表、四行中英文字段插入、带括号/AND/NOT 的 SELECT、DELETE、再次 SELECT；结果必须来自真实 Page/Buffer/Disk，不能是内存字典或 dummy row。

## 8. feature_selection.json

输出使用以下稳定结构：

```json
{
  "schema_version": 1,
  "declared_tasks": ["A-E01", "B-E04", "C-E07", "D-E02"],
  "verified_base_tasks": ["A-B01"],
  "enabled_groups": ["update"],
  "enabled_tasks": ["A-E01", "B-E04", "C-E07", "D-E02"],
  "retained_only_tasks": [],
  "experimental_results": [],
  "skipped_groups": [
    {
      "id": "join",
      "missing_tasks": ["A-E04", "B-E06", "D-E05"],
      "invalid_tasks": [],
      "reason": "not_declared"
    }
  ],
  "contract_hash": "<sha256>",
  "generated_at": "<ISO-8601 with timezone>"
}
```

数组按任务编号或 group id 稳定排序。`enabled_tasks` 只能包含真实复制、测试通过并实际启用的任务。

## 9. integration-report.md

报告至少包含：

- 输入目录、四份 delivery 摘要与契约哈希；
- 用户声明的任务、实际发现的实现和测试证据；
- 24 项基础任务逐项状态；
- 每个扩展组的 required、declared、missing、invalid、测试与最终状态；
- 未声明扩展被排除的证据；
- 适配器的文件、符号、原因和测试；
- 每条命令、环境版本、退出码和关键结果；
- 核心演示、跨页、替换、dirty 写回、DELETE、Catalog 和重启恢复结果；
- 输出路径、`feature_selection.json` 路径与仍存在的实验限制。

只有 24 项基础任务、核心模块测试和核心端到端场景全部通过时，报告才可以标记 `CORE: PASS`。扩展状态独立标记 `ENABLED`、`RETAINED_ONLY`、`EXPERIMENTAL` 或 `SKIPPED`。
