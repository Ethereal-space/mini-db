"""Exercise our Tk callbacks against real databases without user input injection."""

import time
import tkinter as tk

import pytest

from minidb.integration.workbench_gui import Workbench
from minidb.integration.workbench_model import WorkbenchSession, analyze


@pytest.fixture
def gui(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"Tk display unavailable: {error}")
    root.withdraw()
    session = WorkbenchSession(tmp_path / "ui.db")
    workbench = Workbench(root, session)
    yield workbench
    workbench.executor.shutdown(wait=True)
    session.close()
    root.after_cancel(workbench.poll_id)
    root.destroy()


def test_results_trace_and_frontend_widgets(gui):
    result = gui.session.execute("CREATE TABLE t(id INT); INSERT INTO t(id) VALUES (7); SELECT id,id FROM t;")
    gui.show_execution(result)
    gui.select_result()
    assert len(gui.result_tree["columns"]) == 2
    assert gui.result_tree.item(gui.result_tree.get_children()[0], "values") == ("7", "7")
    gui.select_stage()
    assert "Token" in gui.trace_text.get("1.0", "end") or "kind" in gui.trace_text.get("1.0", "end")
    gui.show_frontend(analyze("SELECT DISTINCT id FROM t;", ["distinct"]))
    assert "ExtensionStatement" in gui.ast_text.get("1.0", "end")
    assert len(gui.token_tree.get_children()) > 3
    assert [gui.tabs.tab(tab, "text") for tab in gui.tabs.tabs()] == [
        "01  SQL 工作台", "02  前端分析", "03  Python 存储演示", "04  B 编译器验收"
    ]


def test_b_acceptance_tab_shows_test_source_and_real_evidence(gui):
    assert len(gui.acceptance_selector.cget("values")) == 4
    gui.acceptance_selector.current(0)
    gui.acceptance_selected()
    source = gui.acceptance_code.get("1.0", "end-1c")
    assert "def test_" in source
    gui.show_acceptance_result({
        "case_id": "lexical",
        "passed": True,
        "expected": "Token 符合预期。",
        "tests": [{"name": "test_keyword_identifier", "status": "PASSED"}],
        "summary": "32 passed in 0.10s",
        "evidence": "实际 Token（共 3 个）\n1 | CREATE | CREATE | create | 1:1-1:7",
        "conclusion": "词法分析测试通过。",
    })
    result = gui.acceptance_result.get("1.0", "end")
    assert "预期结果" in result
    assert "实际 Token（共 3 个）" in result
    assert "CREATE | CREATE" in result


def test_background_execution_finishes_and_error_clears_old_results(gui):
    gui.editor.delete("1.0", "end")
    gui.editor.insert("1.0", "CREATE TABLE t(id INT); SELECT * FROM t;")
    gui.execute()
    assert gui.busy
    deadline = time.monotonic() + 10
    while gui.busy and time.monotonic() < deadline:
        gui.root.update()
        time.sleep(0.01)
    assert not gui.busy
    assert len(gui.results) == 2
    error = gui.session.execute("SELECT unknown FROM t;")
    gui.show_execution(error)
    assert gui.results == []
    assert not gui.result_tree.get_children()
    assert "SEMANTIC" in gui.trace_text.get("1.0", "end")


def test_reconnect_clears_stale_result_and_refreshes_catalog(gui):
    gui.show_execution(gui.session.execute("CREATE TABLE t(id INT);"))
    gui.changed_database(gui.session.reconnect(policy="fifo", capacity=2))
    assert gui.results == []
    assert gui.result_select.get() == ""
    assert len(gui.catalog.get_children()) == 1


def test_storage_rubric_python_source_editor_is_editable(gui):
    gui.update_rubric_cases()
    assert gui.rubric_source.cget("state") == "normal"
    gui.rubric_source.insert("end", "\nprint('GUI_EDIT_MARKER')\n")
    assert "GUI_EDIT_MARKER" in gui.rubric_source.get("1.0", "end-1c")
    assert gui.rubric_source.tag_ranges("py_comment")
    assert gui.rubric_source.tag_ranges("py_keyword")
    assert gui.rubric_source.tag_ranges("py_string")
