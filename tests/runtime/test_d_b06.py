from __future__ import annotations

from io import StringIO
from pathlib import Path

from minidb.contracts import (
    ExecutionResult,
    Position,
    Span,
    SyntaxError as MiniDBSyntaxError,
    TraceEvent,
)
from minidb.runtime.cli_service import main


class FakeApp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.error: Exception | None = None

    def execute(self, source: str, trace: bool) -> list[ExecutionResult]:
        self.calls.append((source, trace))
        if self.error is not None:
            raise self.error
        events = (TraceEvent("EXECUTION", "Fake trace"),) if trace else ()
        return [ExecutionResult(message="执行完成", trace=events)]


class FakeContext:
    def __init__(self, app: FakeApp) -> None:
        self.app = app
        self.exit_count = 0

    def __enter__(self) -> FakeApp:
        return self.app

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.exit_count += 1
        return False


class RecordingFactory:
    def __init__(self, app: FakeApp) -> None:
        self.app = app
        self.calls: list[tuple[str, str, int]] = []
        self.contexts: list[FakeContext] = []

    def __call__(self, db_path: str, policy: str, capacity: int) -> FakeContext:
        self.calls.append((db_path, policy, capacity))
        context = FakeContext(self.app)
        self.contexts.append(context)
        return context


def _streams(source: str = "") -> tuple[StringIO, StringIO, StringIO]:
    return StringIO(source), StringIO(), StringIO()


def test_db06_stdin() -> None:
    source = "SELECT 'a;b';\nSELECT 2;"
    app = FakeApp()
    factory = RecordingFactory(app)
    stdin, stdout, stderr = _streams(source)

    code = main([], factory, stdin, stdout, stderr)

    assert code == 0
    assert app.calls == [(source, False)]
    assert factory.calls == [("mini.db", "lru", 16)]
    assert factory.contexts[0].exit_count == 1
    assert stderr.getvalue() == ""


def test_db06_utf8(tmp_path: Path) -> None:
    sql_file = tmp_path / "中文.sql"
    source = "SELECT '张三';"
    sql_file.write_text(source, encoding="utf-8")
    app = FakeApp()
    factory = RecordingFactory(app)
    stdin, stdout, stderr = _streams()

    code = main(["--file", str(sql_file)], factory, stdin, stdout, stderr)

    assert code == 0
    assert app.calls == [(source, False)]
    assert chr(0xFFFD) not in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_db06_trace() -> None:
    app = FakeApp()
    factory = RecordingFactory(app)
    stdin, stdout, stderr = _streams("SELECT 1;")

    code = main(["--trace", "--policy", "fifo", "--capacity", "3"], factory, stdin, stdout, stderr)

    assert code == 0
    assert app.calls == [("SELECT 1;", True)]
    assert factory.calls == [("mini.db", "fifo", 3)]
    assert "TRACE" in stdout.getvalue()
    assert "Fake trace" in stdout.getvalue()


def test_db06_error() -> None:
    app = FakeApp()
    span = Span(Position(10, 3, 2), Position(11, 3, 3))
    app.error = MiniDBSyntaxError("EXPECTED_TOKEN", "缺少 FROM", span)
    factory = RecordingFactory(app)
    stdin, stdout, stderr = _streams("SELECT")

    code = main([], factory, stdin, stdout, stderr)

    assert code == 1
    assert stdout.getvalue() == ""
    assert "SYNTAX" in stderr.getvalue()
    assert "第3行" in stderr.getvalue()
    assert "缺少 FROM" in stderr.getvalue()
    assert factory.contexts[0].exit_count == 1


def test_db06_bad_capacity() -> None:
    app = FakeApp()
    factory = RecordingFactory(app)
    stdin, stdout, stderr = _streams("SELECT 1;")

    code = main(["--capacity", "0"], factory, stdin, stdout, stderr)

    assert code == 2
    assert factory.calls == []
    assert "必须为正整数" in stderr.getvalue()


def test_db06_encoding_error(tmp_path: Path) -> None:
    sql_file = tmp_path / "utf8.sql"
    sql_file.write_text("SELECT '张三';", encoding="utf-8")
    app = FakeApp()
    factory = RecordingFactory(app)
    stdin, stdout, stderr = _streams()

    code = main(
        ["--file", str(sql_file), "--encoding", "ascii"],
        factory,
        stdin,
        stdout,
        stderr,
    )

    assert code == 1
    assert factory.calls == []
    assert "读取 SQL 源失败" in stderr.getvalue()


def test_db06_interactive_meta_commands_and_multiple_statements() -> None:
    app = FakeApp()
    factory = RecordingFactory(app)
    stdin, stdout, stderr = _streams(".help\nSELECT a; SELECT b;\n.quit\n")

    code = main(["--interactive"], factory, stdin, stdout, stderr)

    assert code == 0
    assert [source for source, _trace in app.calls] == ["SELECT a;", "SELECT b;"]
    assert "交互模式" in stdout.getvalue()
    assert stdout.getvalue().count("执行完成") == 2
    assert stderr.getvalue() == ""


def test_db06_interactive_keeps_semicolon_inside_string_and_supports_multiline() -> None:
    app = FakeApp()
    factory = RecordingFactory(app)
    source = "SELECT 'a;b'\nFROM demo;\n.exit\n"
    stdin, stdout, stderr = _streams(source)

    code = main(["--interactive"], factory, stdin, stdout, stderr)

    assert code == 0
    assert app.calls == [("SELECT 'a;b'\nFROM demo;", False)]
    assert "...>" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_db06_interactive_rejects_file_combination() -> None:
    app = FakeApp()
    factory = RecordingFactory(app)
    stdin, stdout, stderr = _streams("SELECT a;")

    code = main(["--interactive", "--file", "input.sql"], factory, stdin, stdout, stderr)

    assert code == 2
    assert factory.calls == []
    assert "不能与 --file 同时使用" in stderr.getvalue()
