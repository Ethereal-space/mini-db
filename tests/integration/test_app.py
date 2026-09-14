from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from minidb.contracts import (
    ExecutionError,
    LexicalError,
    SemanticError,
    SyntaxError,
    UnsupportedError,
)
from minidb.frontend import Frontend
from minidb.integration.app import MiniDBApplication, open_application
from minidb.runtime.cli_service import main as cli_main


CORE_SQL = """
CREATE TABLE student(id INT, name VARCHAR, age INT);
INSERT INTO student(id,name,age) VALUES (1,'Alice',20);
INSERT INTO student(id,name,age) VALUES (2,'Bob',17);
INSERT INTO student(id,name,age) VALUES (3,'Tom',22);
INSERT INTO student(id,name,age) VALUES (4,'张三',21);
SELECT id,name FROM student WHERE 1 = 1 AND age >= 18 AND NOT (name = 'Tom');
DELETE FROM student WHERE id = 1;
SELECT * FROM student;
"""


def test_core_pipeline_trace_and_statement_isolation(tmp_path: Path) -> None:
    database = tmp_path / "core.db"

    with open_application(str(database), "lru", 2) as app:
        results = app.execute(CORE_SQL, trace=True)

    assert len(results) == 8
    assert results[5].columns == ("id", "name")
    assert results[5].rows == ((1, "Alice"), (4, "张三"))
    assert results[6].affected_rows == 1
    assert results[7].rows == ((2, "Bob", 17), (3, "Tom", 22), (4, "张三", 21))

    stages = {event.stage for event in results[5].trace}
    assert {"TOKEN", "AST", "SEMANTIC", "BOUND", "PLAN", "OPTIMIZATION", "OPTIMIZED_PLAN", "EXECUTION"} <= stages
    assert any("Project" in event.detail for event in results[5].trace if event.stage == "PLAN")
    assert any("Project" in event.detail for event in results[5].trace if event.stage == "OPTIMIZED_PLAN")

    # 每条结果只包含本条 AST；前一条 CREATE 不应串入 INSERT 的快照。
    insert_ast = [event.detail for event in results[1].trace if event.stage == "AST"]
    assert len(insert_ast) == 1
    assert "InsertStmt" in insert_ast[0]
    assert "CreateTableStmt" not in insert_ast[0]


def test_reopen_restores_catalog_and_rows(tmp_path: Path) -> None:
    database = tmp_path / "restart.db"
    with open_application(database, "fifo", 3) as app:
        app.execute(
            "CREATE TABLE t(id INT, name VARCHAR);"
            "INSERT INTO t VALUES (1,'one');"
            "INSERT INTO t VALUES (2,'二');"
        )

    with open_application(database, "lru", 2) as app:
        assert [table.name for table in app.catalog.list_tables()] == ["t"]
        result = app.execute("SELECT * FROM t;")[0]
        assert result.rows == ((1, "one"), (2, "二"))
        assert app.storage.disk.next_table_id == 2


def test_application_rejects_errors_with_stage_and_location(tmp_path: Path) -> None:
    database = tmp_path / "errors.db"
    with open_application(database) as app:
        with pytest.raises(SyntaxError) as syntax:
            app.execute("SELECT * FROM t WHERE id > ;")
        assert syntax.value.stage == "SYNTAX"
        assert syntax.value.span is not None

        with pytest.raises(SemanticError) as semantic:
            app.execute("SELECT * FROM missing;")
        assert semantic.value.code == "TABLE_NOT_FOUND"
        assert semantic.value.span is not None
        assert semantic.value.span.start.column == 15

        with pytest.raises(LexicalError) as lexical:
            app.execute("SELECT * FROM t @")
        assert lexical.value.stage == "LEXICAL"
        assert lexical.value.span is not None


def test_enabled_extension_is_explicitly_rejected_by_core_runtime(tmp_path: Path) -> None:
    with MiniDBApplication(tmp_path / "extension.db", enabled_extensions={"update"}) as app:
        with pytest.raises(UnsupportedError) as error:
            app.execute("UPDATE t SET id=1;")
        assert error.value.code == "UNSUPPORTED_FEATURE"
        assert error.value.context == {"feature": "update", "version": 1}


def test_application_lifecycle_and_cli(tmp_path: Path) -> None:
    database = tmp_path / "cli.db"
    stdin = StringIO("CREATE TABLE cli(id INT); INSERT INTO cli VALUES (9); SELECT * FROM cli;")
    stdout = StringIO()
    stderr = StringIO()

    code = cli_main(
        ["--db", str(database), "--trace", "--capacity", "2"],
        open_application,
        stdin,
        stdout,
        stderr,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    assert "已创建表 cli" in stdout.getvalue()
    assert "| id |" in stdout.getvalue()
    assert stdout.getvalue().count("返回 1 行") == 1
    assert "[OPTIMIZED_PLAN]" in stdout.getvalue()

    app = open_application(database)
    app.close()
    with pytest.raises(ExecutionError) as error:
        app.execute("SELECT * FROM cli;")
    assert error.value.code == "APPLICATION_CLOSED"


def test_frontend_core_still_returns_all_statements() -> None:
    statements = Frontend().parse("SELECT id FROM t; SELECT id FROM t;")
    assert len(statements) == 2
