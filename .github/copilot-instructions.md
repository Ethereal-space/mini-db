# GitHub Copilot：MiniDB 仓库说明

统一入口是 `/docs/ai/AI_START_HERE.md`，并遵守 `/AGENTS.md`。若请求包含任务编号，用 `/docs/tasks/catalog.json` 查找唯一任务卡，并把其中 `AI_TASK_BEGIN` 到 `AI_TASK_END` 视为完整需求。

只建议任务卡授权路径内的改动。普通成员不能改 `/minidb/contracts/`、`/contracts.sha256`、其他成员目录和 `/minidb/integration/`。成员模块只能依赖 `/minidb/contracts/` 与标准库。不得建议 sqlite3、SQLAlchemy、Lark、PLY、dummy row、假返回或测试专用生产分支。

代码与注释采用 UTF-8；标识符使用英文，关键算法、错误原因和不变量使用清楚的中文说明。新增行为必须有正常、边界和错误测试，错误保留阶段、Span 与原因。

完成后更新本人 walkthrough 与 delivery JSON。不执行 Git 提交或推送。
