# MiniDB AI 唯一开工入口

本文件是所有仓库型 AI 的统一入口。`AGENTS.md`、`CLAUDE.md`、GitHub Copilot 与 Cursor 规则都指向这里；规则有歧义时，先执行冻结契约和任务卡中更窄的授权范围。

## 单句启动协议

成员只需发送一句话：

```text
我是成员 A，执行任务 A-B01。
```

同样接受“进行任务”“完成任务”，成员字母、标点和空格可省略或变化。例如 `我是成员B进行任务B-E04`。推荐解析表达式为：

```regex
^\s*我是\s*成员\s*([A-D])\s*[，,]?\s*(?:执行|进行|完成)\s*任务\s*([A-D]-[BE]\d{2})\s*[。.]?\s*$
```

捕获组 1 是声明成员，捕获组 2 是任务编号。任务编号首字母必须与成员一致；`B` 表示基础任务，`E` 表示扩展任务。身份不一致时指出具体冲突并停止该任务，不猜测用户想改哪一个编号。

## 收到任务后的固定流程

1. 读取根目录 `AGENTS.md`、本文件、`docs/PROJECT_SPEC.md`、`docs/INTERFACES.md` 和 `docs/tasks/catalog.json`。
2. 在 `catalog.tasks` 中按 `id` 精确匹配一项，读取其 `card` 指向的文件，从 `AI_TASK_BEGIN <id>` 一直读到 `AI_TASK_END <id>`。
3. 核对 `TASK_META` 的 `member`、`id`、`kind`、`schema=1`，再核对前置任务、允许修改、禁止修改、输入输出、公共接口、测试命令和交付文件。
4. 检查当前源码、测试、本人 walkthrough 与 `delivery/member_<x>.json`，区分已经完成、部分完成和缺失内容。
5. 本成员前置缺失时，在当前任务授权路径内补齐最小必要前置。跨成员能力不得复制或导入另一成员实现，必须使用冻结 Protocol 和放在 tests 中的 Fake。
6. 修改前先向用户说明任务理解、原理、文件范围、输入输出、不变量、异常路径和测试计划；随后持续实施直至卡片验收完成。
7. 完成源码与测试后，运行卡片命令，更新真实 walkthrough 和本人 delivery 记录，并按卡片的“代码讲解提示词”解释实际代码。
8. 不执行 Git 提交或推送。GitHub 仓库只保存共同基线，成员开发方式不属于任务卡的强制流程。

## 固定权限边界

| 成员 | 可负责模块 | 独立测试输入 |
| --- | --- | --- |
| A | `minidb/frontend/`、`tests/frontend/` | SQL 文本、冻结 Token/AST |
| B | `minidb/compiler/`、`tests/compiler/` | 手工 AST、Fake Catalog/Repository |
| C | `minidb/storage/`、`tests/storage/` | TableMeta、字节页、临时数据库文件 |
| D | `minidb/runtime/`、`tests/runtime/` | 手工 Plan、Fake Storage/Application |

每张卡还允许对应的 `docs/walkthrough/member_<x>.md` 和 `delivery/member_<x>.json`。若卡片列出的范围更窄，以卡片为准。普通成员不得改 `minidb/contracts/`、`contracts.sha256`、其他成员目录、`minidb/integration/` 或 `tests/integration/`。

成员模块只能依赖 Python 标准库、自己的包与 `minidb.contracts`。生产代码不得导入 tests 或 Fake，不得用 sqlite3、SQLAlchemy、Lark、PLY 或其他现成 SQL/数据库实现替代实验代码。

## 实现与验收底线

- 参考环境精确为 CPython 3.14.2、pip 25.3、pytest 9.1.1；项目声明 `>=3.14,<3.15`，文本统一 UTF-8，核心页固定 4096 字节。
- 不留下 `pass`、`TODO`、`FIXME`、dummy row、假成功、硬编码测试答案或测试专用生产分支。Protocol 中合法的 `...` 与明确抛出的领域异常不算占位。
- 错误保留阶段、具体原因和可用上下文。SQL 错误保留真实 Span；存储错误使用页号、RID 或文件偏移，不伪造 SQL 位置。
- 测试覆盖正常、边界和错误路径，并断言结果结构、状态变化、资源释放或异常字段。只断言“不报错”不能验收任务。
- 扩展任务必须由用户明确点名。扩展跨层交换只使用冻结的版本化信封，不直接扩充核心数据类或 Protocol。
- `parse_expression`、`resolve_column`、`build_plan`、`fetch_page`、`choose_victim`、`scan`、`evaluate`、`execute` 所在任务必须按任务卡要求重点讲解真实控制流和测试证据。

## 完成输出格式

完成任务时至少报告：

1. 实际修改文件和关键符号；
2. 每条验证命令、退出码和结果；
3. 正常路径、边界、异常路径与不变量的证据；
4. 冻结契约哈希与跨成员导入检查结果；
5. walkthrough 与 `delivery/member_<x>.json` 的更新位置；
6. 理解题答案和小修改练习结果；
7. 尚未满足的前置或整合条件。不得把扩展局部通过描述成全链路已启用。

## 最终整合入口

只有收到“你是最终整合 AI”及四份输入目录时，才读取 `docs/integration/INTEGRATION_PLAYBOOK.md`。最终整合以用户声明的已完成扩展为允许清单，以 `docs/integration/feature-groups.json` 判定原子依赖；未声明扩展忽略，部分扩展组跳过，基础核心仍可继续。整合输出写入 `_integration_output/mini-db/`，同样不执行 Git 提交或推送。
