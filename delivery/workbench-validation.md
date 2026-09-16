# 可视化工作台验证记录

日期：2026-09-15。Windows，Python 3.14.2，pytest 9.1.1。

最终界面为四个页面：SQL 工作台、前端分析、页与缓存、使用指南。原任务目录与界面内测试入口已移除，项目自动化测试保留。

验证命令：`python -X utf8 -m pytest -q -p no:cacheprovider --tb=short`。

本地四页版本：382 项测试通过，无跳过。验证覆盖中文 SQL、执行追踪、错误处理、持久化、页观察和 GUI 后台回调。
1000 行演示实际执行 1002 条语句，筛选返回 800 行，占 9 个页；重启后恢复 1000 行。

上传版本使用相同引擎和四页界面，启动脚本改为仓库根目录相对路径，默认数据库在 data/workbench.db。

完整 SQL 链支持 CREATE/INSERT/SELECT/DELETE；UPDATE、ORDER BY/LIMIT、DISTINCT 仅解析展示。
