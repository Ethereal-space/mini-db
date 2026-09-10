# Claude 在 MiniDB 中的入口

统一入口是 `docs/ai/AI_START_HERE.md`；同时遵守根目录 `AGENTS.md`。

收到“我是成员 X，执行/进行/完成任务 X-Bnn/X-Enn”时：从 `docs/tasks/catalog.json` 精确定位任务卡，完整读取 `AI_TASK_BEGIN` 至 `AI_TASK_END`，只修改卡片列出的允许路径，先补强行为测试，再实现并运行卡片命令。不要改冻结 contracts、其他成员目录或 integration。完成后依据真实代码更新本成员 walkthrough 与 delivery JSON，并报告逐条验收证据；不执行 Git 提交或推送。

运行时代码只用 Python 标准库；禁止用现成 SQL 解析器或数据库替代项目实现。环境目标为 Python 3.14.2、pip 25.3、pytest 9.1.1，所有文本采用 UTF-8。
