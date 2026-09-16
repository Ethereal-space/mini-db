# Member B Acceptance Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 MiniDB Studio 中增加成员 B 的四项 SQL 编译器验收下拉面板，选择测试后显示真实测试代码并自动运行真实 pytest，在右侧显示实时结果。

**Architecture:** 在 integration 层增加不可变验收用例配置和后台 pytest 运行函数。GUI 新增独立的“B 编译器验收”标签页，选择事件只更新代码区并提交一个后台任务；任务结果通过已有 Tk 队列回到主线程，填充真实 stdout、stderr 和退出码。编译器、contracts、storage、runtime 和其他成员目录保持不变。

**Tech Stack:** Python 3.14、Tkinter/ttk、标准库 `subprocess`/`threading`/`queue`、pytest、现有 WorkbenchSession 后台任务机制。

## Global Constraints

- 使用 Python 3.14.2、pip 25.3、pytest 9.1.1；当前仓库允许 Python 3.14.x。
- 只修改 integration 工作台、integration 测试和已有验收资料；不修改 `minidb/contracts/`、其他成员模块或 integration 以外的生产模块。
- 测试结果必须来自真实 pytest 子进程，禁止在 GUI 中硬编码通过结果。
- Tk 控件只在主线程更新，测试子进程只能在后台任务中运行。
- 测试运行使用仓库根目录和当前解释器，不能依赖用户数据库或测试 Fake 以外的生产假数据。
- 不留下 `pass`、`TODO`、`FIXME`、dummy row 或假成功结果。

---

### Task 1: Add acceptance-test model and runner

**Files:**
- Modify: `minidb/integration/workbench_model.py`
- Test: `tests/integration/test_workbench.py`

**Interfaces:**
- Consumes: repository root `SOURCE`, existing `WorkbenchSession` and Python `subprocess`.
- Produces: immutable `B_ACCEPTANCE_CASES` configuration and `WorkbenchSession.run_acceptance_case(case_id: str) -> dict`.

- [ ] **Step 1: Write the failing tests**

Add tests that assert the model exposes exactly four stable cases named `lexical`, `syntax`, `semantic`, and `plan`; each case has a non-empty title, code, pytest command, and conclusion. Add a test that calls `run_acceptance_case("lexical")` and asserts the result contains `case_id`, `returncode`, `stdout`, `stderr`, `passed`, `command`, and `conclusion`, with `returncode == 0` for the real lexical test.

```python
def test_b_acceptance_cases_are_stable():
    assert tuple(B_ACCEPTANCE_CASES) == ("lexical", "syntax", "semantic", "plan")
    assert all(B_ACCEPTANCE_CASES[key].code.strip() for key in B_ACCEPTANCE_CASES)

def test_run_acceptance_case_returns_real_pytest_output():
    result = WorkbenchSession(tmp_path / "acceptance.db").run_acceptance_case("lexical")
    assert result["returncode"] == 0
    assert result["passed"] is True
    assert "passed" in result["stdout"]
```

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/integration/test_workbench.py -k acceptance
```

Expected: FAIL because `B_ACCEPTANCE_CASES` and `run_acceptance_case` do not exist yet.

- [ ] **Step 3: Implement the minimal model and runner**

Define a frozen case record with `case_id`, `title`, `code`, `command`, and `conclusion`. Configure the four cases with real test paths:

```python
lexical: tests/frontend/test_a_b01.py tests/frontend/test_a_b02.py
syntax: tests/frontend/test_a_b03.py tests/frontend/test_a_b04.py tests/frontend/test_a_b05.py tests/frontend/test_a_b06.py
semantic: tests/compiler/test_b_b01.py tests/compiler/test_b_b02.py tests/compiler/test_b_b03.py tests/compiler/test_b_b04.py
plan: tests/compiler/test_b_b05.py tests/compiler/test_b_b06.py
```

`run_acceptance_case` must validate the case id, call `subprocess.run` with `[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths]`, use `cwd=SOURCE`, `text=True`, `encoding="utf-8"`, `capture_output=True`, and return the real exit code and streams. Set `passed` from `returncode == 0`; do not replace or summarize away the captured output.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/integration/test_workbench.py -k acceptance
```

Expected: the new acceptance model tests pass and the stdout contains pytest's actual passing summary.

- [ ] **Step 5: Commit the model task**

```powershell
git add minidb/integration/workbench_model.py tests/integration/test_workbench.py
git commit -m "feat: add member B acceptance test runner"
```

### Task 2: Add the B acceptance GUI panel

**Files:**
- Modify: `minidb/integration/workbench_gui.py`
- Test: `tests/integration/test_workbench_gui.py`

**Interfaces:**
- Consumes: `B_ACCEPTANCE_CASES` and `WorkbenchSession.run_acceptance_case` from Task 1, plus the existing `submit`/poll queue mechanism.
- Produces: a new tab titled `05  B 编译器验收` with a readonly `ttk.Combobox`, a left code `Text`, and a right result `Text`.

- [ ] **Step 1: Write the failing GUI tests**

Add tests that instantiate the existing Workbench fixture and assert the new tab exists, the combobox has exactly four values, selecting a value copies its configured code into the code text, and the result callback renders a real return code and output. Keep the test independent of a visible desktop by invoking the callback with a captured runner result where the existing GUI test setup already supports headless Tk.

```python
def test_b_acceptance_tab_has_four_cases(gui):
    labels = [gui.tabs.tab(tab_id, "text") for tab_id in gui.tabs.tabs()]
    assert "05  B 编译器验收" in labels
    assert len(gui.acceptance_selector.cget("values")) == 4

def test_b_acceptance_selection_shows_code(gui):
    gui.acceptance_selector.current(0)
    gui.acceptance_selected()
    assert "tests/frontend/test_a_b01.py" in gui.acceptance_code.get("1.0", "end-1c")
```

- [ ] **Step 2: Run the focused GUI tests and verify the expected failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/integration/test_workbench_gui.py -k acceptance
```

Expected: FAIL because the new tab and widgets do not exist.

- [ ] **Step 3: Implement the panel using the existing background queue**

Add `build_acceptance()` to create the tab and widgets. Keep the combobox readonly and bind `<<ComboboxSelected>>` to `acceptance_selected`. On selection, resolve the case id from a stable ordered tuple, put the configured code and command into the left text, clear the right text, and call the existing `submit` method with `run_acceptance_case`. The result callback must render:

The result view must contain the labels `状态`, `退出码`, `命令`, `标准输出`, `标准错误`, and `结论`, followed by the unchanged values returned by the runner.

Use the existing busy flag to disable duplicate launches. Update all Tk widgets only from the main-thread callback. A non-zero result must show `失败` and preserve both output streams.

- [ ] **Step 4: Run GUI tests and verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/integration/test_workbench_gui.py -k acceptance
```

Expected: all acceptance GUI tests pass.

- [ ] **Step 5: Commit the GUI task**

```powershell
git add minidb/integration/workbench_gui.py tests/integration/test_workbench_gui.py
git commit -m "feat: add member B acceptance panel"
```

### Task 3: Update acceptance documentation and run regression verification

**Files:**
- Modify: `docs/acceptance_member_b.md`
- Test: `tests/integration/test_workbench.py`, `tests/integration/test_workbench_gui.py`

**Interfaces:**
- Consumes: the four GUI case titles, commands, and output labels from Tasks 1 and 2.
- Produces: accurate user instructions for the new tab and a reproducible verification record.

- [ ] **Step 1: Update the acceptance guide**

Document the exact interaction: launch `run_gui.py`, open `05  B 编译器验收`, choose one of four entries, read the left code panel, wait for the right result panel, and repeat for the other entries. State that the right side is live pytest output and that a failure is displayed as failure, not converted to success. Keep the existing SQL import workflow documented separately.

- [ ] **Step 2: Run targeted and full verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/integration/test_workbench.py tests/integration/test_workbench_gui.py
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --ignore=tests/compiler/test_b_e05.py
.\.venv\Scripts\python.exe -m compileall -q minidb
git diff --check
```

Expected: focused integration tests pass, the committed test suite passes without the local unuploaded B-E05 test, compileall exits 0, and diff check has no output.

- [ ] **Step 3: Inspect the running workbench**

Run:

```powershell
.\.venv\Scripts\python.exe run_gui.py
```

Verify manually that the new tab appears, each selection changes the left code, the right panel changes from running to a real pytest summary, and all five tabs still work.

- [ ] **Step 4: Commit the documentation and final regression**

```powershell
git add docs/acceptance_member_b.md tests/integration/test_workbench.py tests/integration/test_workbench_gui.py minidb/integration/workbench_model.py minidb/integration/workbench_gui.py
git commit -m "docs: document member B acceptance panel"
```
