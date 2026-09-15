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


def test_results_trace_frontend_and_page_widgets(gui):
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
    gui.show_page(gui.session.page(result["snapshot"]["tables"][0]["first_page_id"]))
    assert len(gui.slots.get_children()) == 1
    assert gui.page_canvas.find_all()
    assert [gui.tabs.tab(tab, "text") for tab in gui.tabs.tabs()] == [
        "01  SQL 工作台", "02  前端分析", "03  页与缓存", "04  使用指南"
    ]


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
    assert gui.policy.get() == "fifo"
    assert len(gui.catalog.get_children()) == 1
