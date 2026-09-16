# MiniDB 可视化工作台

运行仓库根目录的 `run_gui.py`，或使用 Python 3.14 在源码根目录运行：

```powershell
python -X utf8 -m minidb.integration.workbench_gui
```

工作台调用真实 `MiniDBApplication`、前端解析器和存储观测接口，无第三方数据库替代实现。

## 入口与能力

| 页面 | 能力 |
| --- | --- |
| SQL 工作台 | SQL 导入/保存，CREATE/INSERT/SELECT/DELETE，中文结果，CSV 导出，Catalog，逐语句 Trace，语法/语义错误 |
| 前端分析 | Token 及位置、AST、可开关的 UPDATE/ORDER BY/LIMIT/DISTINCT 解析 |
| 页与缓存 | LRU/FIFO 与容量，刷盘和重连，命中/缺失/淘汰/读写，dirty/pin，缓冲事件，真实页字节、空间分布、槽位与 tombstone |
| 使用指南 | 推荐演示顺序、执行边界、持久化与文件位置 |

“完整演示”和“1000 行跨页演示”每次创建独立数据库。已存在的工作库不被覆盖。新建拒绝覆盖已有路径。

当前下载版仅有核心四种 SQL 的完整链路。UPDATE、ORDER BY/LIMIT、DISTINCT 仅提供前端解析展示。

## 实现与错误语义

`workbench_model.py` 负责应用生命周期、观察快照和解析；`workbench_gui.py` 负责 Tk 界面及单工作线程调度。数据库任务串行执行，UI 通过队列接收结果，工作线程不操作 Tk。错误包含原始阶段和位置；界面最外层异常边界保存完整堆栈。

保持原整合应用的语义：完整脚本先解析，语法失败不写库；后续语义/执行错误保留之前成功语句。应用抛出异常时不返回已执行语句的结果，界面明确提示可能已生效并刷新 Catalog，不显示伪造的部分结果。核心无事务回滚。

缓存快照不触发 fetch，不改变置换顺序；页面优先读取缓存，否则读取磁盘。刷盘与重连通过正式应用接口执行。

## 验证

```powershell
python -X utf8 -m pytest -q -p no:cacheprovider
```

新增测试覆盖真实读写、重连、错误原子性和部分提交、槽位删除标记、观察不改变缓存统计、重复演示保留原库、损坏文件恢复连接、解析扩展边界、GUI 结果/Trace/页面填充和后台回调。无显示器环境下 GUI 测试明确跳过。
