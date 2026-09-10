# MiniDB 仓库型 AI 工作规则

## 任务路由

当用户只给出“我是成员 A，执行任务 A-B01”这类指令时，立即执行以下流程。“执行”“进行”“完成”和可选空格均按 `docs/ai/AI_START_HERE.md` 的单句协议识别。

1. 读取 `docs/ai/AI_START_HERE.md`。
2. 读取 `docs/tasks/catalog.json`，用任务编号定位唯一 `card`。
3. 完整读取该任务卡中 `AI_TASK_BEGIN` 到 `AI_TASK_END` 的内容。
4. 校验任务编号首字母与成员身份一致；不一致时明确指出，不猜测替换。
5. 先检查当前实现和测试，再按任务卡实施、验证并解释真实代码。

不得因为指令简短而要求用户重复任务卡内容。任务卡已经是该任务的完整工作说明。

## 固定边界

- 成员 A 只负责 `minidb/frontend/`、`tests/frontend/` 和 `docs/walkthrough/member_a.md`。
- 成员 B 只负责 `minidb/compiler/`、`tests/compiler/` 和 `docs/walkthrough/member_b.md`。
- 成员 C 只负责 `minidb/storage/`、`tests/storage/` 和 `docs/walkthrough/member_c.md`。
- 成员 D 只负责 `minidb/runtime/`、`tests/runtime/` 和 `docs/walkthrough/member_d.md`。
- `minidb/contracts/` 与 `contracts.sha256` 是冻结契约，普通成员任务只读。
- `minidb/integration/` 和 `tests/integration/` 仅用于集中整合。
- 成员模块只能导入 `minidb.contracts` 和 Python 标准库；不能直接导入另一成员模块。
- 生产代码不能依赖测试 Fake，不能使用 sqlite3、SQLAlchemy、Lark、PLY 或其他 SQL/数据库实现替代实验代码。

若任务卡的“允许修改”比成员通用边界更窄，以任务卡为准。扩展任务只能在明确点名时实施，不能顺手扩大核心范围。

## 实现与验证

开始修改前，输出任务理解、将修改的文件、输入输出、不变量、错误路径和新增测试。实现时不得留下 `pass`、`TODO`、`FIXME`、dummy row、假成功结果或针对固定测试值的生产分支。合法的 Protocol 方法省略号和有意抛出的领域异常不算占位实现。

错误必须保留阶段、位置和原因。不要用宽泛 `Exception` 隐藏内部错误；资源释放需要覆盖正常、错误和提前停止路径。测试至少覆盖正常、边界和错误输入，并断言结果结构、状态变化或异常字段，不能只断言“不报错”。

统一环境：Python 3.14.2、pip 25.3、pytest 9.1.1、UTF-8。每张卡的命令是最低验证集；目录已完整装配时再运行全量测试。

完成后必须：

- 列出实际修改文件与关键符号。
- 报告每条验证命令和结果。
- 按任务卡逐个解释类、函数、状态变化、分支、异常及上下游契约。
- 更新本人 walkthrough，写入真实实现、真实测试名和可复现调用过程。
- 更新 `delivery/member_<x>.json`，写入完成任务、实际文件、命令结果、契约哈希与 walkthrough 路径。
- 回答任务卡的理解题，并完成其中的小型修改练习或说明其独立练习方式。
- 不执行 Git 提交或推送；仓库不规定成员的提交、分支、同步方式。

## 集中整合

集中整合只读取 `_integration_input/member_a`、`_integration_input/member_b`、`_integration_input/member_c`、`_integration_input/member_d`，并遵循 `docs/integration/INTEGRATION_PLAYBOOK.md`。24 项基础任务必须齐全。扩展能力按 `docs/integration/feature-groups.json` 原子选择；未列出的扩展输入忽略。
