"""MiniDB 核心运行时的集中装配入口。

成员模块保持独立实现，只在本模块把 Frontend、Compiler、Storage 和
Runtime 通过冻结 contracts 串起来。应用按语句顺序编译、优化和执行，
因此一个脚本中的 CREATE 会立即对后续 INSERT/SELECT 可见；文件、页缓存
和 Catalog 的生命周期也由这个对象统一管理。
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import replace
from pathlib import Path

from minidb.compiler.catalog_service import CatalogService
from minidb.compiler.optimizer import OptimizationReport, optimize
from minidb.compiler.plan_formatter import format_plan
from minidb.compiler.planner import Compiler
from minidb.contracts import (
    ApplicationPort,
    ExecutionError,
    ExecutionResult,
    ExtensionStatement,
    Span,
    Token,
    TokenKind,
    TraceEvent,
    UnsupportedError,
)
from minidb.frontend import Frontend, Lexer
from minidb.frontend.formatter import format_ast, format_tokens
from minidb.runtime.execution_service import ExecutionService
from minidb.storage.catalog_repository import PageCatalogRepository
from minidb.storage.table_heap import TableHeap


def _normalize_policy(policy: str) -> str:
    if not isinstance(policy, str):
        raise TypeError("policy 必须是 lru 或 fifo")
    normalized = policy.casefold()
    if normalized not in {"lru", "fifo"}:
        raise ValueError("policy 必须是 lru 或 fifo")
    return normalized


def _tokens_for_statement(source_tokens: Sequence[Token], statement: object) -> tuple[Token, ...]:
    """按语句 Span 从整批 Token 中切出独立快照，并补一个局部 EOF。"""

    span = getattr(statement, "span")
    selected = tuple(
        token
        for token in source_tokens
        if token.kind is not TokenKind.EOF
        and span.start.offset <= token.span.start.offset
        and token.span.end.offset <= span.end.offset
    )
    # 不把后一条语句或整批 EOF 串进当前结果；调用方只读这些不可变 Token。
    eof_span = Span(span.end, span.end)
    return selected + (Token(TokenKind.EOF, "", None, eof_span),)


class MiniDBApplication(ApplicationPort):
    """拥有完整 MiniDB 生命周期的 ApplicationPort 实现。"""

    def __init__(
        self,
        db_path: str | Path = "mini.db",
        policy: str = "lru",
        capacity: int = 16,
        *,
        enabled_extensions: Collection[str] = (),
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity 必须是正整数")
        self._db_path = Path(db_path)
        self._policy = _normalize_policy(policy)
        self._capacity = capacity
        self._enabled_extensions = frozenset(enabled_extensions)
        self._closed = False

        storage: TableHeap | None = None
        try:
            storage = TableHeap(
                self._db_path,
                buffer_pool_size=capacity,
                policy=self._policy,
            )
            repository = PageCatalogRepository(storage)
            catalog = CatalogService(
                repository,
                initial_next_table_id=storage.disk.next_table_id,
            )
            self._storage = storage
            self._repository = repository
            self._catalog = catalog
            self._executor = ExecutionService(catalog, storage)
        except BaseException:
            if storage is not None:
                storage.close()
            raise

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def catalog(self) -> CatalogService:
        return self._catalog

    @property
    def storage(self) -> TableHeap:
        return self._storage

    @property
    def closed(self) -> bool:
        return self._closed

    def _check_open(self) -> None:
        if self._closed:
            raise ExecutionError("APPLICATION_CLOSED", "MiniDB 应用已经关闭")

    def _frontend_events(
        self,
        statement: object,
        tokens: Sequence[Token],
    ) -> tuple[TraceEvent, ...]:
        selected = _tokens_for_statement(tokens, statement)
        return (
            TraceEvent("TOKEN", format_tokens(selected)),
            TraceEvent("AST", format_ast(statement)),
        )

    def execute(self, source: str, trace: bool = False) -> list[ExecutionResult]:
        """执行完整 SQL 脚本；解析失败时不执行任何语句。"""

        self._check_open()
        if not isinstance(source, str):
            raise TypeError("source 必须为 str")

        # Frontend.parse 先完成整批词法和语法检查，所以语法错误不会留下
        # 前半批的磁盘副作用。Trace 需要时再用同一源码切分逐语句 Token。
        statements = Frontend(enabled_extensions=self._enabled_extensions).parse(source)
        source_tokens = Lexer(source).tokenize() if trace else ()
        results: list[ExecutionResult] = []

        for statement in statements:
            if isinstance(statement, ExtensionStatement):
                raise UnsupportedError(
                    "UNSUPPORTED_FEATURE",
                    f"核心运行时未注册扩展 {statement.feature} v{statement.version}",
                    statement.span,
                    {"feature": statement.feature, "version": statement.version},
                )

            compiler_events: list[TraceEvent] = []
            compiler = Compiler(on_trace=compiler_events.append)
            plan = compiler.compile(statement, self._catalog)
            report = OptimizationReport()
            optimized = optimize(plan, report)

            result = self._executor.execute(optimized)
            if not trace:
                # ApplicationPort 的 false 模式必须返回无 Trace 的快照，避免
                # 调用方误把上一次调试信息当作普通结果。
                results.append(replace(result, trace=()))
                continue

            optimization_events = (
                TraceEvent("OPTIMIZATION", f"rules={report.counts}"),
                TraceEvent("OPTIMIZED_PLAN", format_plan(optimized)),
            )
            prefix = self._frontend_events(statement, source_tokens)
            results.append(
                replace(
                    result,
                    trace=prefix + tuple(compiler_events) + optimization_events + result.trace,
                )
            )
        return results

    def flush(self) -> None:
        self._check_open()
        self._storage.flush_all()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._storage.close()
        finally:
            self._closed = True

    def __enter__(self) -> "MiniDBApplication":
        self._check_open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()


def open_application(
    db_path: str = "mini.db",
    policy: str = "lru",
    capacity: int = 16,
) -> MiniDBApplication:
    """供 CLI 使用的三参数工厂，签名与 ``runtime.cli_service`` 一致。"""

    return MiniDBApplication(db_path, policy, capacity)


__all__ = ["MiniDBApplication", "open_application"]
