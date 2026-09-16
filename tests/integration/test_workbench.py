import pytest

from minidb.contracts import StorageError

from minidb.integration.workbench_model import RUBRIC_CASES, WorkbenchSession, analyze


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


def test_scoring_rubric_cases_have_passing_evidence(session):
    """The built-in scoring page must exercise the real implementation."""

    assert len(RUBRIC_CASES) == 3
    assert all(case.source_type == "python" and "存储系统" in case.source for case in RUBRIC_CASES)
    for case in RUBRIC_CASES:
        outcome = session.run_rubric_case(case.case_id)
        assert outcome["passed"], f"{case.case_id}: {outcome['actual']}"
        assert outcome["case"]["rubric"] == case.rubric
        assert all(item["passed"] for item in outcome["checks"])


def test_scoring_actual_output_is_python_stdout(session):
    storage = session.run_rubric_case("STORAGE-BUFFER-01")
    assert "access policy=lru" in storage["raw_output"]
    assert "policy=fifo stats=" in storage["raw_output"]


def test_storage_rubric_executes_current_python_source(session):
    case = next(item for item in RUBRIC_CASES if item.case_id == "STORAGE-PAGE-01")
    edited = case.source + "\nprint('EDITED_STORAGE_SOURCE_MARKER')\n"
    outcome = session.run_rubric_case(case.case_id, source=edited)
    assert outcome["source"] == edited
    assert "EDITED_STORAGE_SOURCE_MARKER" in outcome["raw_output"]
    assert outcome["passed"]


def test_storage_rubric_shows_python_output_when_recipe_is_incomplete(session):
    outcome = session.run_rubric_case(
        "STORAGE-PAGE-01",
        source="print('custom page probe')",
    )
    assert not outcome["passed"]
    assert "custom page probe" in outcome["raw_output"]
    assert "缺少验收变量" in outcome["raw_output"]
