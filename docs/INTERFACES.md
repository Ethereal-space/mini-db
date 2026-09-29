# MiniDB 冻结接口说明

本文件解释 `minidb/contracts/` 的公共边界。Python 源文件与 `contracts.sha256` 是可执行真值；本文用于阅读和独立实现。普通成员不得修改契约来适配自己的实现。

## 1. 七个运行端口

```python
from collections.abc import Iterator
from typing import Protocol

class FrontendPort(Protocol):
    def parse(self, source: str) -> list[Statement]: ...

class CatalogPort(Protocol):
    def table_exists(self, name: str) -> bool: ...
    def get_table(self, name: str) -> TableMeta: ...
    def allocate_table_id(self) -> int: ...
    def register_table(self, table: TableMeta) -> None: ...
    def list_tables(self) -> list[TableMeta]: ...

class CatalogRepositoryPort(Protocol):
    def load_tables(self) -> list[TableMeta]: ...
    def save_table(self, table: TableMeta) -> None: ...

class CompilerPort(Protocol):
    def compile(self, statement: Statement, catalog: CatalogPort) -> PlanNode: ...
    def optimize(self, plan: PlanNode) -> PlanNode: ...

class StoragePort(Protocol):
    def create_table(self, table_id: int, columns: tuple[ColumnMeta, ...]) -> int: ...
    def insert(self, table: TableMeta, values: tuple[int | str, ...]) -> RID: ...
    def scan(self, table: TableMeta) -> Iterator[StoredRow]: ...
    def mark_delete(self, table: TableMeta, rid: RID) -> None: ...
    def flush_all(self) -> None: ...
    def stats(self) -> BufferStats: ...

class ExecutorPort(Protocol):
    def execute(self, plan: PlanNode) -> ExecutionResult: ...

class ApplicationPort(Protocol):
    def execute(self, source: str, trace: bool) -> list[ExecutionResult]: ...
```

Protocol 只约束结构，不提供生产实现。tests 中的 Fake 可以实现最小端口供单成员测试；生产代码不得导入 Fake。调用方只能依赖这些方法及其返回契约，不能探测另一成员对象的私有属性。

## 2. 源位置与 Token

- `Position(offset, line, column)`：offset 为零基 Unicode 码点位置，行列为一基。
- `Span(start, end)`：左闭右开，end 不得早于 start；EOF 使用零宽 Span。
- `Token(kind, lexeme, value, span)`：lexeme 保留原文；value 保存解码字符串、Python 数值、规范标识符或 None。
- 核心关键字与扩展关键字均有预留 TokenKind；出现预留 Token 不代表对应扩展已启用。

Token、AST、Bound AST、Plan 和结果对象使用 frozen dataclass 与 tuple，跨层不共享可变 list。

## 3. AST

核心表达式：`Literal`、`Identifier`、`UnaryExpr`、`BinaryExpr`。核心语句：

```text
CreateTableStmt(name, columns, span)
InsertStmt(table, columns | None, values, span)
SelectStmt(table, columns | None, where | None, span)
DeleteStmt(table, where | None, span)
```

Select 的 `columns=None` 唯一表示 `*`；不能使用名称为 `"*"` 的 Identifier。Insert 的 `columns=None` 表示省略列列表，与显式空 tuple 不同。Parser 保留用户顺序和 Span，不检查表/列是否存在，不分配页，也不执行 SQL。

## 4. 元数据与 Bound AST

`DataType` 枚举包含 INT、VARCHAR 和只供表达式中间结果使用的 BOOL；核心存储列只接受 INT 与 VARCHAR，ColumnMeta 使用 BOOL 时必须拒绝。`ColumnMeta(name, dtype, ordinal)` 的 name 必须已规范化，ordinal 从 0 连续。`TableMeta(table_id, name, columns, first_page_id)` 的 table_id 和首页都大于等于 1，列不能为空、名称不能重复。

绑定表达式只使用 `BoundLiteral`、`BoundColumn`、`BoundUnary`、`BoundBinary`。`BoundColumn.index` 是 schema 中的稳定下标，执行器按下标取值，不能再次按列名查询 Catalog。Bound 节点必须保留原 AST Span。

绑定语句：

```text
BoundCreate(name, columns, span)
BoundInsert(table, values, span)
BoundSelect(table, indices, names, where, span)
BoundDelete(table, where, span)
```

BoundInsert 的值已经按 schema 重排并完成类型与长度检查。BoundSelect 的 `indices` 与 `names` 等长，星号已展开；重复投影列仍可出现。

## 5. 逻辑计划

核心计划节点是 `CreateTablePlan`、`InsertPlan`、`SeqScanPlan`、`FilterPlan`、`ProjectPlan`、`DeletePlan`。计划是无副作用的数据描述：编译 CREATE 时不能分配表号或页，编译 SELECT/DELETE 时不能扫描真实表。

`ProjectPlan.indices` 与 `names` 等长且下标非负。`DeletePlan.child` 必须产生包含 RID 的同表行。优化返回新的不可变计划，不能原地改输入，也不能改变语义结果或丢失可诊断 Span。

## 6. 存储与执行结果

- `RID(page_id, slot_id)`：page_id 大于等于 1，slot_id 大于等于 0；删除后 RID 不重编号。
- `StoredRow(rid, values)`：扫描结果同时携带物理定位和值 tuple。
- `ExecutionResult(columns, rows, affected_rows, message, trace)`：含行时必须有 columns，每行宽度等于 columns，affected_rows 非负。
- `TraceEvent(stage, detail)`：stage 非空，每次执行返回自己的不可变事件 tuple。
- `BufferStats(hits, misses, evictions, reads, writes)`：所有计数非负，表示累计物理缓存行为。
- `BufferEvent(action, page_id, detail)`：用于 C 模块的可读事件快照，不改变 `StoragePort.stats()` 签名。

`scan()` 返回的 iterator 在正常结束、异常和调用方提前停止时都要释放 pin。执行器不得保存指向可变 frame 的引用作为结果行。

## 7. 错误契约

公共错误基类为 `MiniDBError(stage, code, message, span=None, context=None)`，各阶段可定义专用子类。机器使用 stage/code，用户阅读 message，定位使用 Span 或 context。

| 阶段 | 典型责任 |
| --- | --- |
| LEXICAL | 非法字符、非法数字、未闭合字符串/注释 |
| SYNTAX | 实际 Token、期望集合、尾随输入 |
| SEMANTIC | 表/列不存在、歧义、类型与范围错误 |
| PLANNING | 无法构造合法逻辑计划 |
| OPTIMIZATION | 优化规则契约被破坏 |
| EXECUTION | 表达式、算子或影响行数失败 |
| STORAGE / IO | 页、RID、缓存、文件与持久化失败 |
| UNSUPPORTED | 核心未启用的类型、语法或扩展版本 |

只能捕获预期的领域错误。`IndexError`、`KeyError`、断言失败等内部缺陷不能被统一改写成“SQL 错误”。

## 8. 版本化扩展信封

```text
ExtensionStatement(feature, version, payload, span)
BoundExtension(feature, version, payload, span)
ExtensionPlan(feature, version, payload, span)
```

`feature` 必须是非空小写规范名，`version>=1`，payload 的键是字符串，值只含 `str | int | float | bool | None | list | dict`。任何 AST/Bound/Plan 节点放入 payload 前都要编码为 JSON 对象；禁止把 Python dataclass 实例塞进 payload。

未知 feature/version 必须返回 UNSUPPORTED，不能猜测字段。扩展只在 `docs/integration/feature-groups.json` 的条件满足时进入最终装配。

## 9. 生命周期与装配

CatalogService 从 Repository 加载 TableMeta，并把 `max(已持久化最大表号 + 1, initial_next_table_id)` 作为下一候选表号。Storage 创建表后返回首页，Catalog 随后注册并持久化完整 TableMeta；失败可以留下表号空洞，不能注册半成品表。

最终应用工厂负责对象所有权、flush 和 close。`ApplicationPort` 保持最小运行接口，不因具体实现增加生命周期方法。适配器只能包装返回值、上下文管理或调用约定，不得改变语义、页格式或冻结模型。

## 10. 契约完整性

`contracts.sha256` 由仓库工具按相对路径和 LF 规范化后的 UTF-8 字节稳定生成，因此 Windows 与 Linux 得到相同结果。成员交付必须记录同一哈希。最终整合发现任一输入的冻结目录或哈希不一致时，拒绝该成员输入并报告差异；不能选择某一份修改后的契约作为新基线。
