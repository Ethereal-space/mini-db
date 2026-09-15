"""Read-only observations and application actions for the desktop workbench."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import time

from minidb.contracts import MiniDBError
from minidb.frontend import Frontend, Lexer
from minidb.frontend.formatter import format_ast, format_tokens
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

    def close(self):
        self.app.close()
