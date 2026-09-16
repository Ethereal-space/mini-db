import pytest

from minidb.contracts import StorageError

from minidb.integration.workbench_model import B_ACCEPTANCE_CASES, WorkbenchSession, analyze


@pytest.fixture
def session(tmp_path):
    with_session = WorkbenchSession(tmp_path / "workbench.db")
    yield with_session
    with_session.close()


def test_execute_trace_and_reopen(session):
    result = session.execute("CREATE TABLE t(id INT, name VARCHAR); INSERT INTO t(id,name) VALUES (1,'中文'); SELECT * FROM t;")
    assert result["error"] is None
    assert result["results"][-1]["rows"] == ((1, "中文"),)
    stages = {event["stage"] for event in result["results"][-1]["trace"]}
    assert {"TOKEN", "AST", "PLAN", "OPTIMIZED_PLAN", "EXECUTION"} <= stages
    session.reconnect(policy="fifo", capacity=2)
    assert session.execute("SELECT * FROM t;")["results"][0]["rows"] == ((1, "中文"),)


def test_parse_error_atomicity_and_semantic_partial_execution(session):
    failed = session.execute("CREATE TABLE invalid(id INT); SELECT >;")
    assert failed["error"] and failed["position"]
    assert failed["snapshot"]["tables"] == []
    partial = session.execute("CREATE TABLE kept(id INT); SELECT missing FROM kept;")
    assert "SEMANTIC" in partial["error"]
    assert [t["name"] for t in partial["snapshot"]["tables"]] == ["kept"]
    assert session.execute("SELECT * FROM kept;")["error"] is None


def test_page_observation_tombstone_and_flush(session):
    session.execute("CREATE TABLE t(id INT); INSERT INTO t(id) VALUES (1); INSERT INTO t(id) VALUES (2); DELETE FROM t WHERE id = 1;")
    before = session.snapshot()
    page_id = before["tables"][0]["first_page_id"]
    page = session.page(page_id)
    assert len(page["slots"]) == 2
    assert sum(slot["deleted"] for slot in page["slots"]) == 1
    assert session.snapshot()["stats"] == before["stats"]
    assert "Superblock" in session.page(0)["description"]
    assert session.flush()["frames"]
    assert not any(frame["dirty"] for frame in session.snapshot()["frames"])
    with pytest.raises(ValueError):
        session.page(-1)


def test_repeat_demo_keeps_original_database(session):
    original = session.path
    session.execute("CREATE TABLE original(id INT);")
    first = session.isolated_demo()
    assert first["error"] is None
    first_path = session.path
    assert first_path != original
    second = session.isolated_demo()
    assert second["error"] is None
    assert session.path != first_path
    assert original.exists() and first_path.exists()
    session.reconnect(original)
    assert session.snapshot()["tables"][0]["name"] == "original"


def test_reconnect_invalid_database_restores_session(session, tmp_path):
    session.execute("CREATE TABLE t(id INT);")
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"broken")
    with pytest.raises(StorageError):
        session.reconnect(broken)
    assert session.execute("SELECT * FROM t;")["error"] is None
    with pytest.raises(ValueError):
        session.reconnect(capacity=0)


def test_frontend_extensions_are_analysis_only(session):
    parsed = analyze("SELECT DISTINCT age FROM student;", ["distinct"])
    assert parsed["error"] is None
    assert parsed["tokens"]
    assert parsed["ast"][0]["kind"] == "ExtensionStatement"
    assert analyze("SELECT DISTINCT age FROM student;")["error"]
    assert session.snapshot()["tables"] == []


def test_lexical_failure_replaces_previous_analysis():
    result = analyze("SELECT @ FROM student;")
    assert "LEXICAL" in result["error"]
    assert result["tokens"] == []
    assert result["ast"] == []


def test_b_acceptance_cases_are_stable():
    assert tuple(B_ACCEPTANCE_CASES) == ("lexical", "syntax", "semantic", "plan")
    assert all(case.title and case.code.strip() and case.command for case in B_ACCEPTANCE_CASES.values())


def test_run_acceptance_case_returns_real_pytest_output(tmp_path):
    session = WorkbenchSession(tmp_path / "acceptance.db")
    try:
        result = session.run_acceptance_case("lexical")
    finally:
        session.close()
    assert result["case_id"] == "lexical"
    assert result["returncode"] == 0
    assert result["passed"] is True
    assert "passed" in result["stdout"]
    assert result["command"]
    assert result["conclusion"]
