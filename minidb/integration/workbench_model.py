"""Read-only observations and application actions for the desktop workbench.

The workbench's fourth page is a focused Python storage demonstration.  Its
recipes are data driven so the GUI can show the source, expected checks and
evidence produced by the real MiniDB storage implementation without copying a
second implementation into the UI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from types import MappingProxyType

from minidb.compiler.plan_formatter import format_plan
from minidb.compiler.semantic import analyze_select
from minidb.contracts import MiniDBError
from minidb.contracts.bound import BoundBinary, BoundColumn, BoundLiteral, BoundUnary
from minidb.frontend import Frontend, Lexer
from minidb.frontend.formatter import format_ast, format_tokens
from minidb.storage.constants import PAGE_SIZE
from minidb.storage.page import Page
from .app import MiniDBApplication


SOURCE = Path(__file__).resolve().parents[2]
EXAMPLES = {
    "01 · 创建学生表": "CREATE TABLE student(id INT, name VARCHAR, age INT);",
    "02 · 插入中文数据": "INSERT INTO student(id,name,age) VALUES (1,'张三',18);\nINSERT INTO student(id,name,age) VALUES (2,'李四',17);\nINSERT INTO student(id,name,age) VALUES (3,'Alice',22);",
    "03 · 查询与布尔条件": "SELECT id,name FROM student\nWHERE age >= 18 AND NOT (name = 'Alice');",
    "04 · 常量折叠与优化": "SELECT name FROM student WHERE 1 = 1 AND age >= 18;",
    "05 · 删除与 tombstone": "DELETE FROM student WHERE id = 2;\nSELECT * FROM student;",
    "06 · 语法错误定位": "SELECT name FROM student WHERE age > ;",
    "07 · 语义错误定位": "SELECT missing_column FROM student;",
    "08 · UPDATE（仅解析）": "UPDATE student SET age = 19 WHERE id = 1;",
    "09 · 排序分页（仅解析）": "SELECT name FROM student ORDER BY age DESC LIMIT 2;",
    "10 · DISTINCT（仅解析）": "SELECT DISTINCT age FROM student;",
}


@dataclass(frozen=True, slots=True)
class RubricCase:
    """One repeatable demonstration item from the scoring document."""

    case_id: str
    group: str
    rubric: str
    title: str
    purpose: str
    source: str
    source_type: str
    expected: str
    checks: tuple[str, ...]


RUBRIC_GROUPS = ("存储系统",)


# 04 Python 存储演示只保留评分文档中“存储系统”基本功能的三类证据。
# 每段 source 都是可直接复制到 Python 解释器运行的完整脚本；工作台只注入
# DATABASE_PATH，脚本中的 print 才是右侧“实际输出”的唯一来源。
RUBRIC_CASES: tuple[RubricCase, ...] = (
    RubricCase(
        "STORAGE-PAGE-01", "存储系统", "页式存储管理（4分）", "页面分配、释放与恢复",
        "验证 4096 字节页面的追加、读写、空闲链和重启后复用。",
        "# 存储系统页面管理验收：这是评分文档要求的预设测试程序，检查页面分配、释放、读取、写入和数据恢复。\n"
        "# DATABASE_PATH 由工作台注入；不要改成固定路径，避免污染正式工作库。\n"
        "from minidb.storage.disk_manager import DiskManager\n"
        "from minidb.storage.constants import PAGE_SIZE\n\n"
        "# 1. 打开新数据库并追加一页，验证 page_id 和整页写入。\n"
        "path = DATABASE_PATH\n"
        "with DiskManager.open(path) as disk:\n"
        "    page_id = disk.allocate_page()\n"
        "    payload = bytes([0xA5]) * PAGE_SIZE\n"
        "    disk.write_page(page_id, payload)\n"
        "    read_back = disk.read_page(page_id)\n"
        "    read_back_equal = read_back == payload\n"
        "    print(f'page_id={page_id}')\n"
        "    print(f'payload_bytes={len(payload)}')\n"
        "    print(f'read_back_equal={read_back_equal}')\n"
        "\n"
        "    # 2. 释放刚才的页；free_list 应记录可再次分配的页号。\n"
        "    disk.free_page(page_id)\n"
        "    free_chain_before_reopen = disk.free_list\n"
        "    print(f'free_chain_before_reopen={free_chain_before_reopen}')\n"
        "\n"
        "# 3. 关闭后重新打开同一个文件，证明空闲链已经持久化。\n"
        "with DiskManager.open(path) as disk:\n"
        "    reopened_chain = disk.free_list\n"
        "    reused_page_id = disk.allocate_page()\n"
        "    reused_bytes = disk.read_page(reused_page_id)\n"
        "    page_count = disk.page_count\n"
        "    print(f'reopened_chain={reopened_chain}')\n"
        "    print(f'reused_page_id={reused_page_id}')\n"
        "    print(f'page_count={page_count}')\n"
        "    print(f'reused_page_is_zero={reused_bytes == bytes(PAGE_SIZE)}')\n"
        "\n"
        "# 4. 文件长度必须是完整页的整数倍，供验收检查页式 I/O 边界。\n"
        "file_size = path.stat().st_size\n"
        "file_aligned = file_size % PAGE_SIZE == 0\n"
        "print(f'file_size={file_size}')\n"
        "print(f'file_aligned={file_aligned}')",
        "python",
        "文件长度始终按 4096 字节对齐；写入页读回字节完全一致；释放后 free_list 含该页；\n"
        "重启后再次分配得到同一个 page_id，证明空闲页状态已持久化。",
        ("文件长度为整页", "页读写字节一致", "释放进入空闲链", "重启后复用原 page_id"),
    ),
    RubricCase(
        "STORAGE-BUFFER-01", "存储系统", "缓存机制（4分）", "LRU 与 FIFO 访问序列",
        "用固定 A B A C 序列观察容量为 2 的命中、缺失、淘汰和牺牲页。",
        "# 存储系统缓存验收：这是评分文档要求的预设测试程序，检查固定访问序列下的命中、淘汰、替换和回写。\n"
        "# 访问序列 A B A C，容量固定为 2；观察值由真实 BufferPool.stats/events 产生。\n"
        "from minidb.storage.disk_manager import DiskManager\n"
        "from minidb.storage.buffer_pool import BufferPool\n\n"
        "observations = {}\n"
        "with DiskManager.open(DATABASE_PATH) as disk:\n"
        "    # 先准备三个真实磁盘页，后面的访问只通过 BufferPool 完成。\n"
        "    pages = [disk.allocate_page() for _ in range(3)]\n"
        "    print(f'pages={pages}')\n"
        "    for policy in ('lru', 'fifo'):\n"
        "        pool = BufferPool(disk, pool_size=2, policy=policy)\n"
        "        try:\n"
        "            # A、B、A、C：第三次访问 A 应命中，最后一次触发一次淘汰。\n"
        "            for page_id in (pages[0], pages[1], pages[0], pages[2]):\n"
        "                with pool.get_page(page_id) as frame:\n"
        "                    print(f'access policy={policy} page={page_id} bytes={len(frame.data)}')\n"
        "            stats = pool.stats()\n"
        "            victims = [event.page_id for event in pool.events if event.action == 'evict']\n"
        "            actions = [event.action for event in pool.events]\n"
        "            observations[policy] = (stats, victims, actions)\n"
        "            print(f'policy={policy} stats={stats!r} victims={victims!r} actions={actions!r}')\n"
        "        finally:\n"
        "            # 关闭缓存，确保脏页和资源按正式生命周期处理。\n"
        "            pool.close()",
        "python",
        "两种策略均为 hits=1、misses=3、evictions=1；LRU 淘汰 B，FIFO 淘汰 A；\n"
        "事件记录包含 read、hit、evict，且 dirty 写回状态可见。",
        ("LRU 统计正确", "FIFO 统计正确", "LRU 牺牲页为 B", "FIFO 牺牲页为 A", "缓冲事件可追踪"),
    ),
    RubricCase(
        "STORAGE-INTEGRATION-01", "存储系统", "接口与集成（4分）", "get_page/write_page 与上层衔接",
        "验证统一页面接口返回固定页、原样写回，并能被数据库上层读取。",
        "# 存储系统接口集成验收：这是评分文档要求的预设测试程序，检查 get_page()/write_page() 和上层数据库衔接。\n"
        "# 脚本同时使用 MiniDBApplication、BufferPool 和 DiskManager，结果全部现场读取。\n"
        "from minidb.integration.app import MiniDBApplication\n"
        "from minidb.storage.constants import PAGE_SIZE\n"
        "from minidb.runtime.result_formatter import format_result\n\n"
        "with MiniDBApplication(DATABASE_PATH) as app:\n"
        "    # 先用正式数据库接口写入一行，得到真实的数据页。\n"
        "    app.execute('CREATE TABLE student(id INT); INSERT INTO student VALUES (7);')\n"
        "    table = app.catalog.get_table('student')\n"
        "\n"
        "    # 1. BufferPool.get_page 返回固定大小的数据帧。\n"
        "    with app.storage.buffer_pool.get_page(table.first_page_id) as frame:\n"
        "        fetched_bytes = len(frame.data)\n"
        "\n"
        "    # 2. DiskManager.write_page 原样写回后再次读取，比较完整页字节。\n"
        "    raw_before = app.storage.disk.read_page(table.first_page_id)\n"
        "    app.storage.disk.write_page(table.first_page_id, raw_before)\n"
        "    raw_after = app.storage.disk.read_page(table.first_page_id)\n"
        "\n"
        "    # 3. 通过上层 SQL 接口读取同一页中的行，确认接口链路没有破坏数据。\n"
        "    query_result = app.execute('SELECT * FROM student;')[0]\n"
        "    print(f'page_size={PAGE_SIZE}')\n"
        "    print(f'get_page_bytes={fetched_bytes}')\n"
        "    print(f'write_page_equal={raw_before == raw_after}')\n"
        "    print(format_result(query_result))",
        "python",
        "get_page 返回 4096 字节；write_page 后页头和行数据不变；\n"
        "上层 SELECT 仍返回 (7)，说明页面接口与 TableHeap/执行器连接正常。",
        ("get_page 返回整页", "write_page 原样回写", "上层 SELECT 结果保持"),
    ),
)

RUBRIC_CASES_BY_ID = {case.case_id: case for case in RUBRIC_CASES}


def rubric_cases(group: str | None = None) -> tuple[RubricCase, ...]:
    """Return immutable rubric metadata, optionally filtered by group."""

    if group is None:
        return RUBRIC_CASES
    if group not in RUBRIC_GROUPS:
        raise ValueError(f"未知评分分组：{group}")
    return tuple(case for case in RUBRIC_CASES if case.group == group)


def analyze(source: str, extensions=()) -> dict:
    if not source.strip():
        raise ValueError("请先输入 SQL。")
    # Token and AST are the real frontend output; no database operations here.
    result = {"tokens": [], "ast": [], "error": None}
    try:
        tokens = Lexer(source).tokenize()
        result["tokens"] = json.loads(format_tokens(tokens))
        statements = Frontend(enabled_extensions=extensions).parse(source)
        result["ast"] = [json.loads(format_ast(item)) for item in statements]
    except MiniDBError as error:
        result["error"] = str(error)
    return result


@dataclass(frozen=True, slots=True)
class AcceptanceCase:
    case_id: str
    title: str
    code: str
    test_paths: tuple[str, ...]
    conclusion: str
    expected: str

    @property
    def test_source(self) -> str:
        return "\n\n".join((SOURCE / path).read_text(encoding="utf-8") for path in self.test_paths)

    @property
    def command(self) -> str:
        return "python -m pytest -vv -p no:cacheprovider " + " ".join(self.test_paths)


B_ACCEPTANCE_CASES = MappingProxyType({
    "lexical": AcceptanceCase("lexical", "词法分析（4分）", "CREATE TABLE student(id INT, name VARCHAR, age INT);\nINSERT INTO student VALUES (1, 'Alice', 20);\nSELECT id, name FROM student WHERE age >= 18;", ("tests/frontend/test_a_b01.py", "tests/frontend/test_a_b02.py"), "验证关键字、标识符、常量、运算符、注释和非法输入定位。", "关键字、标识符、常量、运算符、注释、非法输入及源码位置符合预期。"),
    "syntax": AcceptanceCase("syntax", "语法分析（4分）", "CREATE TABLE student(id INT, name VARCHAR, age INT);\nINSERT INTO student VALUES (1, 'Alice', 20);\nSELECT id, name FROM student WHERE age >= 18;\nDELETE FROM student WHERE id = 1;", ("tests/frontend/test_a_b03.py", "tests/frontend/test_a_b04.py", "tests/frontend/test_a_b05.py", "tests/frontend/test_a_b06.py"), "验证四类核心 SQL 的 AST 构造和典型语法错误。", "CREATE、INSERT、SELECT、DELETE 的 AST 结构及典型语法错误符合预期。"),
    "semantic": AcceptanceCase("semantic", "语义分析（4分）", "SELECT name FROM student WHERE age >= 18;\nSELECT missing FROM student;\nINSERT INTO student(id, name) VALUES (1, 'Alice');", ("tests/compiler/test_b_b01.py", "tests/compiler/test_b_b02.py", "tests/compiler/test_b_b03.py", "tests/compiler/test_b_b04.py"), "验证 Catalog、表列存在性、列绑定、类型和列数检查。", "表和列存在性、列绑定序号、数据类型、列数及错误位置符合预期。"),
    "plan": AcceptanceCase("plan", "执行计划生成（4分）", "SELECT id, name FROM student WHERE age >= 18;\nDELETE FROM student WHERE id = 1;", ("tests/compiler/test_b_b05.py", "tests/compiler/test_b_b06.py"), "验证 Project、Filter、SeqScan、DeletePlan 和优化计划结构。", "Project、Filter、SeqScan、DeletePlan 及优化后的节点关系符合预期。"),
})

_PYTEST_CASE_RE = re.compile(r"::(?P<name>test[^\s]+)\s+(?P<status>PASSED|FAILED|SKIPPED|XFAIL|XPASS)\b")


def _bound_text(value: object) -> str:
    if isinstance(value, BoundColumn):
        return f"{value.name}[序号={value.index}, 类型={value.dtype.value}]"
    if isinstance(value, BoundLiteral):
        return f"{value.value!r}[类型={value.dtype.value}]"
    if isinstance(value, BoundBinary):
        return f"({_bound_text(value.left)} {value.op} {_bound_text(value.right)})"
    if isinstance(value, BoundUnary):
        return f"({value.op} {_bound_text(value.operand)})"
    return "无"


def build_acceptance_evidence(case_id: str) -> str:
    case = B_ACCEPTANCE_CASES[case_id]
    if case_id == "lexical":
        tokens = Lexer(case.code).tokenize()
        lines = [f"实际 Token（共 {len(tokens)} 个）", "序号 | 类型 | 词素 | 值 | 位置", "--- | --- | --- | --- | ---"]
        for number, token in enumerate(tokens, 1):
            start, end = token.span.start, token.span.end
            lines.append(f"{number} | {token.kind.name} | {token.lexeme or 'EOF'} | {token.value if token.value is not None else ''} | {start.line}:{start.column}-{end.line}:{end.column}")
        return "\n".join(lines)
    if case_id == "syntax":
        statements = Frontend().parse(case.code)
        return "实际 AST（共 {} 条语句）\n{}".format(len(statements), json.dumps(json.loads(format_ast(statements)), ensure_ascii=False, indent=2))
    with tempfile.TemporaryDirectory(prefix="acceptance-", dir=SOURCE) as directory:
        app = MiniDBApplication(Path(directory) / "evidence.db")
        try:
            app.execute("CREATE TABLE student(id INT, name VARCHAR, age INT);")
            if case_id == "semantic":
                statement = Frontend().parse("SELECT id, name FROM student WHERE age >= 18;")[0]
                bound = analyze_select(statement, app.catalog)
                table = app.catalog.get_table("student")
                selected = "\n".join(f"{name} | {index} | {table.columns[index].dtype.value}" for name, index in zip(bound.names, bound.indices, strict=True))
                try:
                    app.execute("SELECT missing FROM student;")
                except MiniDBError as error:
                    error_text = f"阶段={error.stage}，代码={error.code}\n{error}"
                return f"实际绑定：\n表：student\n列名 | 序号 | 类型\n--- | --- | ---\n{selected}\nWHERE：{_bound_text(bound.where)}\n\n实际错误：\n{error_text}"
            results = [app.execute(sql, trace=True)[0] for sql in ("SELECT id, name FROM student WHERE age >= 18;", "DELETE FROM student WHERE id = 1;")]
            plans = [(event.stage, event.detail) for result in results for event in result.trace if event.stage in {"PLAN", "OPTIMIZED_PLAN"}]
            return "实际计划：\n" + "\n\n".join(f"计划 {index}（{'原始' if stage == 'PLAN' else '优化后'}）：\n{detail}" for index, (stage, detail) in enumerate(plans, 1))
        finally:
            app.close()


def _check(name: str, passed: bool, evidence: str) -> dict:
    """Create a stable, display-ready evidence record for one rubric item."""

    return {"name": name, "passed": bool(passed), "evidence": evidence}


def _rubric_result(
    case: RubricCase,
    checks: list[dict],
    actual_lines: list[str],
    *,
    source: str | None = None,
    snapshot: dict | None = None,
    effect: dict | None = None,
) -> dict:
    """Convert a runner result to the GUI/JSON shape used by all cases."""

    raw_output = "\n".join(actual_lines)
    return {
        "case_id": case.case_id,
        "case": asdict(case),
        "source": case.source if source is None else source,
        "expected": case.expected,
        "actual": raw_output,
        "raw_output": raw_output,
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
        "snapshot": snapshot,
        "effect": effect or {"columns": (), "rows": (), "message": ""},
    }


def _execute_or_error(app: MiniDBApplication, source: str) -> tuple[str, object]:
    """Run one statement and retain domain errors as evidence instead of hiding them."""

    try:
        return "OK", app.execute(source, trace=True)
    except MiniDBError as error:
        return "ERROR", str(error)


def _execution_effect(result: object | None, *, message: str = "") -> dict:
    """Normalize an ExecutionResult for the rubric result table."""

    if result is None:
        return {"columns": (), "rows": (), "affected_rows": 0, "message": message}
    return {
        "columns": tuple(getattr(result, "columns", ()) or ()),
        "rows": tuple(getattr(result, "rows", ()) or ()),
        "affected_rows": int(getattr(result, "affected_rows", 0) or 0),
        "message": str(getattr(result, "message", "") or message),
    }


def _execute_python_source(source: str, database: Path) -> tuple[str, dict]:
    """Execute an editable storage demonstration and capture its stdout.

    ``DATABASE_PATH`` is injected so every run uses a disposable database.
    The text shown in the left workbench pane is compiled and executed as-is;
    the returned output is exactly what that Python source writes to stdout.
    """

    output = StringIO()
    namespace = {"DATABASE_PATH": database, "__name__": "__minidb_rubric__"}
    with redirect_stdout(output):
        try:
            exec(compile(source, "<minidb-rubric-python>", "exec"), namespace, namespace)
        except Exception as error:
            # An edited recipe is user-facing input.  Keep its real stdout and
            # expose a concise runtime diagnostic as evidence instead of
            # turning a missing variable or syntax error into a GUI crash.
            namespace["__runtime_error__"] = f"{type(error).__name__}: {error}"
            print(f"[PYTHON ERROR] {namespace['__runtime_error__']}")
    return output.getvalue().rstrip(), namespace


def _python_guard(
    case: RubricCase,
    source: str,
    output: str,
    namespace: dict,
    required: tuple[str, ...],
) -> dict | None:
    """Return a useful FAIL result when edited Python lacks its evidence API.

    Storage rubric checks intentionally inspect named values left by the
    recipe.  This guard lets a user experiment with arbitrary Python (for
    example, only printing a page header) and still see that real output and
    a precise missing-field/runtime diagnostic in the result pane.
    """

    runtime_error = namespace.get("__runtime_error__")
    missing = tuple(name for name in required if name not in namespace)
    if runtime_error is None and not missing:
        return None
    reason = str(runtime_error) if runtime_error is not None else "缺少验收变量：" + ", ".join(missing)
    checks = [_check(name, False, reason) for name in case.checks]
    actual = [output or "<Python 代码没有标准输出>", "验收输入错误\n" + reason]
    return _rubric_result(
        case,
        checks,
        actual,
        source=source,
        effect={"columns": (), "rows": (), "affected_rows": 0, "message": reason},
    )


class WorkbenchSession:
    def __init__(self, path: Path, policy="lru", capacity=4):
        self.path = Path(path).resolve()
        self.policy, self.capacity = policy, capacity
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.app = MiniDBApplication(self.path, policy, capacity)

    def execute(self, source: str) -> dict:
        if not source.strip():
            raise ValueError("请先输入 SQL。")
        started = time.perf_counter()
        results, error, position = [], None, None
        try:
            results = [asdict(item) for item in self.app.execute(source, trace=True)]
        except MiniDBError as exc:
            error = str(exc)
            if exc.span:
                position = [exc.span.start.line, exc.span.start.column]
        return {"source": source, "results": results, "error": error, "position": position,
                "seconds": time.perf_counter() - started, "snapshot": self.snapshot()}

    def run_acceptance_case(self, case_id: str) -> dict:
        case = B_ACCEPTANCE_CASES[case_id]
        command = [sys.executable, "-m", "pytest", "-vv", "-p", "no:cacheprovider", *case.test_paths]
        completed = subprocess.run(command, cwd=SOURCE, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False)
        combined = "\n".join((completed.stdout, completed.stderr))
        tests = [{"name": match.group("name"), "status": match.group("status")} for match in _PYTEST_CASE_RE.finditer(combined)]
        summary = next((line.strip() for line in reversed(combined.splitlines()) if re.search(r"\d+\s+(passed|failed|error|skipped)", line)), "没有找到 pytest 汇总。")
        evidence = build_acceptance_evidence(case_id)
        return {"case_id": case_id, "returncode": completed.returncode, "passed": completed.returncode == 0, "command": " ".join(command), "stdout": completed.stdout, "stderr": completed.stderr, "tests": tests, "summary": summary, "expected": case.expected, "evidence": evidence, "conclusion": case.conclusion if completed.returncode == 0 else f"{case.title} 未通过，请查看测试输出。"}

    def snapshot(self) -> dict:
        pool, disk = self.app.storage.buffer_pool, self.app.storage.disk
        return {
            "path": str(self.path), "policy": self.policy, "capacity": self.capacity,
            "tables": [asdict(table) for table in self.app.catalog.list_tables()],
            "stats": asdict(pool.stats()), "pages": disk.page_count,
            "free_pages": sorted(disk.free_pages),
            "frames": [{"page": frame.page_id, "dirty": frame.dirty, "pins": frame.pin_count}
                       for frame in pool.frames.values()],
            "events": [asdict(event) for event in pool.events[-200:]],
        }

    def page(self, page_id: int) -> dict:
        disk, pool = self.app.storage.disk, self.app.storage.buffer_pool
        if page_id < 0 or page_id >= disk.page_count:
            raise ValueError("页号超出当前数据库范围。")
        frame = pool.get_frame(page_id)
        raw = bytes(frame.data) if frame else disk.read_page(page_id)
        result = {"page": page_id, "origin": "缓存中的最新内容" if frame else "磁盘内容",
                  "hex": "\n".join(f"{i:04X}  " + raw[i:i + 16].hex(" ").upper()
                                     for i in range(0, len(raw), 16)), "slots": []}
        if page_id == 0:
            result["description"] = "Superblock\n" + str(disk.superblock)
        elif page_id in disk.free_pages:
            result["description"] = "空闲页；空闲链：" + str(disk.free_list)
        else:
            page = Page.from_bytes(raw, expected_page_id=page_id)
            result.update(free_start=page.free_start, free_end=page.free_end,
                          slots=[dict(id=i, **asdict(slot), deleted=slot.deleted)
                                 for i, slot in enumerate(page.slots)])
            result["description"] = (f"表 ID {page.table_id}  ·  下一页 {page.next_page_id}  ·  "
                                     f"存活记录 {page.live_count} / 槽位 {page.slot_count}  ·  可用 {page.available} B")
        return result

    def flush(self) -> dict:
        self.app.flush()
        return self.snapshot()

    def reconnect(self, path=None, policy=None, capacity=None) -> dict:
        target = Path(path).resolve() if path else self.path
        policy = policy or self.policy
        capacity = self.capacity if capacity is None else capacity
        if policy not in {"lru", "fifo"} or not isinstance(capacity, int) or not 1 <= capacity <= 1024:
            raise ValueError("策略请选择 lru/fifo，缓存容量须为 1–1024 页。")
        # Close before reopening the same file to persist dirty frames.
        old = (self.path, self.policy, self.capacity)
        self.app.close()
        try:
            replacement = MiniDBApplication(target, policy, capacity)
        except (MiniDBError, OSError, ValueError):
            self.app = MiniDBApplication(*old)
            raise
        self.path, self.policy, self.capacity = target, policy, capacity
        self.app = replacement
        return self.snapshot()

    def isolated_demo(self, large=False) -> dict:
        # A new file on every run makes repeatable demos independent of user tables.
        with tempfile.NamedTemporaryFile(prefix="demo-", suffix=".db", dir=self.path.parent, delete=False) as file:
            path = Path(file.name)
        self.reconnect(path)
        count = 1000 if large else 4
        sql = "CREATE TABLE student(id INT, name VARCHAR, age INT);\n"
        sql += "\n".join(f"INSERT INTO student(id,name,age) VALUES ({i},'同学{i}',{16 + i % 10});"
                         for i in range(1, count + 1))
        sql += "\nSELECT * FROM student WHERE age >= 18;"
        return self.execute(sql)

    def _with_temp_app(self, callback):
        """Run a rubric callback in a disposable database without changing this session."""

        with tempfile.TemporaryDirectory(prefix="rubric-", dir=self.path.parent) as directory:
            database = Path(directory) / "case.db"
            with MiniDBApplication(database, self.policy, self.capacity) as app:
                return callback(app, database)

    def _rubric_storage_page(self, case: RubricCase, source: str | None = None) -> dict:
        with tempfile.TemporaryDirectory(prefix="rubric-page-", dir=self.path.parent) as directory:
            database = Path(directory) / "page.db"
            source_text = case.source if source is None else source
            output, namespace = _execute_python_source(source_text, database)
            guarded = _python_guard(
                case, source_text, output, namespace,
                ("page_id", "read_back_equal", "free_chain_before_reopen",
                 "reopened_chain", "reused_page_id", "reused_bytes",
                 "file_size", "file_aligned"),
            )
            if guarded is not None:
                return guarded
            page_id = namespace["page_id"]
            read_back_matches = namespace["read_back_equal"]
            free_chain = namespace["free_chain_before_reopen"]
            reopened_chain = namespace["reopened_chain"]
            reused = namespace["reused_page_id"]
            reused_bytes = namespace["reused_bytes"]
            file_size = namespace["file_size"]
            aligned = namespace["file_aligned"]
            page_count = namespace.get("page_count", "由 Python 代码输出")
            checks = [
                _check("文件长度为整页", aligned, f"{file_size} B / {PAGE_SIZE} B"),
                _check("页读写字节一致", read_back_matches, f"page={page_id}, bytes={len(reused_bytes)}"),
                _check("释放进入空闲链", free_chain == (page_id,), repr(free_chain)),
                _check("重启后复用原 page_id", reopened_chain == (page_id,) and reused == page_id, f"reopen={reopened_chain}, reused={reused}"),
            ]
            actual = [output or "<Python 代码没有标准输出>"]
            effect = {
                "columns": ("指标", "结果"),
                "rows": (
                    ("page_id", page_id),
                    ("page_count", page_count),
                    ("文件长度", f"{file_size} B"),
                    ("read_page == write_page", read_back_matches),
                    ("free_list after reopen", repr(reopened_chain)),
                    ("allocate reused", reused),
                ),
                "affected_rows": 0,
                "message": "页面 API 验收完成",
            }
            return _rubric_result(case, checks, actual, source=source_text, effect=effect)

    def _rubric_storage_buffer(self, case: RubricCase, source: str | None = None) -> dict:
        with tempfile.TemporaryDirectory(prefix="rubric-buffer-", dir=self.path.parent) as directory:
            database = Path(directory) / "buffer.db"
            source_text = case.source if source is None else source
            output, namespace = _execute_python_source(source_text, database)
            guarded = _python_guard(case, source_text, output, namespace, ("pages", "observations"))
            if guarded is not None:
                return guarded
            pages = namespace["pages"]
            observations = namespace["observations"]
            lru_stats, lru_victims, lru_actions = observations["lru"]
            fifo_stats, fifo_victims, fifo_actions = observations["fifo"]
            lru_expected = (lru_stats.hits, lru_stats.misses, lru_stats.evictions) == (1, 3, 1)
            fifo_expected = (fifo_stats.hits, fifo_stats.misses, fifo_stats.evictions) == (1, 3, 1)
            checks = [
                _check("LRU 统计正确", lru_expected, repr(lru_stats)),
                _check("FIFO 统计正确", fifo_expected, repr(fifo_stats)),
                _check("LRU 牺牲页为 B", lru_victims == [pages[1]], repr(lru_victims)),
                _check("FIFO 牺牲页为 A", fifo_victims == [pages[0]], repr(fifo_victims)),
                _check("缓冲事件可追踪", {"read", "hit", "evict"}.issubset(lru_actions + fifo_actions), repr(lru_actions + fifo_actions)),
            ]
            actual = [
                output or "<Python 代码没有标准输出>",
            ]
            effect = {
                "columns": ("策略", "命中", "缺失", "淘汰", "牺牲页"),
                "rows": (
                    ("LRU", lru_stats.hits, lru_stats.misses, lru_stats.evictions, repr(lru_victims)),
                    ("FIFO", fifo_stats.hits, fifo_stats.misses, fifo_stats.evictions, repr(fifo_victims)),
                ),
                "affected_rows": 0,
                "message": "A B A C 缓存序列验收完成",
            }
            return _rubric_result(case, checks, actual, source=source_text, effect=effect)

    def _rubric_storage_integration(self, case: RubricCase, source: str | None = None) -> dict:
        with tempfile.TemporaryDirectory(prefix="rubric-storage-integration-", dir=self.path.parent) as directory:
            database = Path(directory) / "integration.db"
            source_text = case.source if source is None else source
            output, namespace = _execute_python_source(source_text, database)
            guarded = _python_guard(
                case, source_text, output, namespace,
                ("fetched_bytes", "raw_before", "raw_after", "query_result"),
            )
            if guarded is not None:
                return guarded
            fetched_bytes = namespace["fetched_bytes"]
            raw_before = namespace["raw_before"]
            raw_after = namespace["raw_after"]
            query_result = namespace["query_result"]
            rows = query_result.rows
            checks = [
                _check("get_page 返回整页", fetched_bytes == PAGE_SIZE, f"{fetched_bytes} B"),
                _check("write_page 原样回写", raw_before == raw_after, "raw_before == raw_after"),
                _check("上层 SELECT 结果保持", rows == ((7,),), repr(rows)),
            ]
            actual = [output or "<Python 代码没有标准输出>"]
            return _rubric_result(case, checks, actual, source=source_text, effect=_execution_effect(query_result))

    def run_rubric_case(self, case_id: str, *, source: str | None = None) -> dict:
        """Run one storage Python case using the optional edited source."""

        try:
            case = RUBRIC_CASES_BY_ID[case_id]
        except KeyError as error:
            raise ValueError(f"未知评分用例：{case_id}") from error
        runners = {
            "STORAGE-PAGE-01": self._rubric_storage_page,
            "STORAGE-BUFFER-01": self._rubric_storage_buffer,
            "STORAGE-INTEGRATION-01": self._rubric_storage_integration,
        }
        try:
            runner = runners[case_id]
        except KeyError as error:
            raise ValueError(f"不是存储 Python 用例：{case_id}") from error
        if case.source_type != "python":
            raise ValueError(f"用例 {case_id} 不是 Python 存储演示")
        return runner(case, source=source)

    def close(self):
        self.app.close()
