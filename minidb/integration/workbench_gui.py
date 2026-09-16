"""MiniDB desktop workbench: real SQL execution and teaching observations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
import keyword
from pathlib import Path
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import tokenize
import traceback

from minidb.contracts import MiniDBError
from .workbench_model import (
    EXAMPLES,
    RUBRIC_CASES,
    SOURCE,
    WorkbenchSession,
    analyze,
)


BG, NAVY, INK, MUTED, BLUE = "#eef2f7", "#13243c", "#1d304c", "#64748b", "#2563eb"


def pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def set_text(widget, value, *, readonly=True):
    """Replace a text widget's contents and choose its final edit state.

    Most workbench panes are evidence panes and remain read-only.  The
    storage rubric source pane is deliberately left editable so the code
    visible to the user is the code that will be executed by the next run.
    """

    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", value)
    widget.configure(state="disabled" if readonly else "normal")


def text_box(parent, **options):
    return scrolledtext.ScrolledText(parent, font=("Consolas", 10), wrap="word",
                                    relief="flat", padx=12, pady=10,
                                    background="white", foreground=INK, **options)


PYTHON_TAGS = {
    "py_comment": {"foreground": "#6a737d"},
    "py_keyword": {"foreground": "#7c3aed", "font": ("Consolas", 10, "bold")},
    "py_string": {"foreground": "#a31515"},
    "py_number": {"foreground": "#098658"},
    "py_builtin": {"foreground": "#005cc5"},
    "py_operator": {"foreground": "#b45309"},
}


PYTHON_BUILTINS = frozenset({
    "print", "len", "bytes", "str", "int", "tuple", "list", "dict", "set",
    "range", "enumerate", "open", "Path", "True", "False", "None",
})

# Python 3.14 tokenizes f-strings into three token kinds.  Include them in
# the string color while keeping compatibility with interpreters without
# the newer constants.
PYTHON_STRING_TOKEN_TYPES = frozenset({
    token_type
    for token_type in (
        tokenize.STRING,
        getattr(tokenize, "FSTRING_START", -1),
        getattr(tokenize, "FSTRING_MIDDLE", -1),
        getattr(tokenize, "FSTRING_END", -1),
    )
    if token_type >= 0
})


def highlight_python(widget):
    """Apply small Python-IDE style syntax colors to a Tk Text widget.

    The standard-library tokenizer supplies token spans, so colors follow
    Python syntax rather than brittle substring replacement.  Invalid or
    unfinished code is left partially colored and remains fully editable.
    """

    for tag, options in PYTHON_TAGS.items():
        widget.tag_configure(tag, **options)
        widget.tag_remove(tag, "1.0", "end")
    source = widget.get("1.0", "end-1c")
    if not source:
        widget.edit_modified(False)
        return
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            tag = None
            if token.type == tokenize.COMMENT:
                tag = "py_comment"
            elif token.type in PYTHON_STRING_TOKEN_TYPES:
                tag = "py_string"
            elif token.type == tokenize.NUMBER:
                tag = "py_number"
            elif token.type == tokenize.NAME:
                if keyword.iskeyword(token.string):
                    tag = "py_keyword"
                elif token.string in PYTHON_BUILTINS:
                    tag = "py_builtin"
            elif token.type == tokenize.OP:
                tag = "py_operator"
            if tag is not None:
                start = f"{token.start[0]}.{token.start[1]}"
                end = f"{token.end[0]}.{token.end[1]}"
                widget.tag_add(tag, start, end)
    except (tokenize.TokenError, IndentationError):
        # While a user is typing a multiline string or an indented block,
        # tokenize may not yet have a complete program.  Keep the editor
        # usable; the run action will report the real compile error.
        return
    finally:
        widget.edit_modified(False)


def bind_python_highlighting(widget):
    """Install live highlighting without moving the user's caret."""

    for tag, options in PYTHON_TAGS.items():
        widget.tag_configure(tag, **options)

    def on_modified(event=None):
        if widget.edit_modified():
            highlight_python(widget)

    widget.bind("<<Modified>>", on_modified, add="+")
    widget.edit_modified(False)
    highlight_python(widget)


def table(parent, columns, height=10):
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=height)
    for col in columns:
        tree.heading(col, text=col)
        tree.column(col, width=140, minwidth=60)
    vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return tree


def fill(tree, rows):
    tree.delete(*tree.get_children())
    for row in rows:
        tree.insert("", "end", values=row)


class Workbench:
    def __init__(self, root, session):
        self.root, self.session = root, session
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.messages = queue.Queue()
        self.busy = False
        self.results = []
        self.snapshot = {}
        root.title("MiniDB Studio · 数据库可视化工作台")
        root.geometry("1440x920")
        root.minsize(1120, 740)
        root.configure(background=BG)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), foreground=INK)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG)
        style.configure("TButton", padding=(11, 7))
        style.configure("Accent.TButton", background=BLUE, foreground="white")
        style.map("Accent.TButton", background=[("active", "#1d4ed8")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 11))
        style.map("TNotebook.Tab", background=[("selected", "white")], foreground=[("selected", BLUE)])
        style.configure("Treeview", rowheight=29, background="white", fieldbackground="white", borderwidth=0)
        style.configure("Treeview.Heading", background="#e6ecf5", padding=6)
        header = tk.Frame(root, bg=NAVY, padx=22, pady=15)
        header.pack(fill="x")
        tk.Label(header, text="MiniDB  /  STUDIO", font=("Segoe UI", 21, "bold"), bg=NAVY, fg="white").pack(side="left")
        tk.Label(header, text="SQL → 编译优化 → 执行 → 4096 B 页式存储", bg=NAVY, fg="#a9bdd8",
                 font=("Microsoft YaHei UI", 11)).pack(side="right")
        bar = ttk.Frame(root, padding=(16, 10))
        bar.pack(fill="x")
        self.path_label = ttk.Label(bar, text="", width=54)
        self.path_label.pack(side="left", fill="x", expand=True)
        for label, command in [("打开数据库", self.open_db), ("新建", self.new_db), ("刷盘", self.flush), ("重连 / 验证恢复", self.reopen)]:
            ttk.Button(bar, text=label, command=command).pack(side="left", padx=3)
        self.status = tk.StringVar(value="就绪")
        footer = ttk.Frame(root, padding=(18, 8))
        footer.pack(side="bottom", fill="x")
        ttk.Label(footer, textvariable=self.status).pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=130)
        self.progress.pack(side="right")
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.sql_tab = self.tab("01  SQL 工作台")
        self.front_tab = self.tab("02  前端分析")
        self.store_tab = self.tab("03  页与缓存")
        self.rubric_tab = self.tab("04  Python 存储演示")
        self.help_tab = self.tab("05  使用指南")
        self.build_sql()
        self.build_frontend()
        self.build_storage()
        self.build_rubric()
        self.build_help()
        self.refresh(session.snapshot())
        self.load_example()
        root.bind("<F5>", lambda event: self.execute())
        root.bind("<Control-Return>", lambda event: self.execute())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = root.after(80, self.poll)

    def tab(self, name):
        frame = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(frame, text=name)
        return frame

    def submit(self, label, function, callback):
        if self.busy:
            self.status.set("正在执行，请等待当前操作完成。")
            return
        self.busy = True
        self.status.set(label)
        self.progress.start(12)

        def work():
            try:
                result = function()
            except Exception as error:
                # Top-level GUI boundary: show the full failure, never report success.
                self.messages.put((None, error, traceback.format_exc()))
            else:
                self.messages.put((callback, result, None))

        self.executor.submit(work)

    def poll(self):
        try:
            callback, result, details = self.messages.get_nowait()
        except queue.Empty:
            self.poll_id = self.root.after(80, self.poll)
            return
        self.busy = False
        self.progress.stop()
        if details:
            self.status.set("操作失败：" + str(result))
            set_text(self.trace_text, details)
            self.tabs.select(self.sql_tab)
            messagebox.showerror("操作失败", str(result), parent=self.root)
        else:
            self.status.set("操作完成")
            callback(result)
        self.poll_id = self.root.after(80, self.poll)

    def build_sql(self):
        tools = ttk.Frame(self.sql_tab)
        tools.pack(fill="x", pady=(0, 10))
        self.example = ttk.Combobox(tools, values=list(EXAMPLES), state="readonly", width=29)
        self.example.current(0)
        self.example.pack(side="left")
        ttk.Button(tools, text="载入示例", command=self.load_example).pack(side="left", padx=5)
        ttk.Button(tools, text="完整演示（新库）", command=lambda: self.demo(False)).pack(side="left", padx=3)
        ttk.Button(tools, text="1000 行跨页演示（新库）", command=lambda: self.demo(True)).pack(side="left", padx=3)
        ttk.Button(tools, text="导入 SQL", command=self.import_sql).pack(side="right", padx=3)
        ttk.Button(tools, text="保存 SQL", command=self.save_sql).pack(side="right", padx=3)
        body = ttk.Panedwindow(self.sql_tab, orient="horizontal")
        body.pack(fill="both", expand=True)
        sidebar, main, tracing = ttk.Frame(body), ttk.Frame(body), ttk.Frame(body)
        body.add(sidebar, weight=1)
        body.add(main, weight=4)
        body.add(tracing, weight=3)
        ttk.Label(sidebar, text="CATALOG / 表结构", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=6)
        self.catalog = ttk.Treeview(sidebar, show="tree", height=12)
        self.catalog.pack(fill="both", expand=True, padx=(0, 8))
        self.catalog.bind("<Double-1>", self.table_query)
        ttk.Label(sidebar, text="双击表名载入 SELECT\n执行后刷新元数据", foreground=MUTED).pack(anchor="w", pady=12)
        ttk.Label(main, text="SQL 编辑器", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=6)
        self.editor = text_box(main, height=10, undo=True)
        self.editor.pack(fill="both", expand=True, padx=(0, 8))
        self.editor.tag_configure("error", background="#fee2e2", foreground="#991b1b")
        actions = ttk.Frame(main)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="▶ 执行 SQL   F5", style="Accent.TButton", command=self.execute).pack(side="left")
        ttk.Button(actions, text="仅分析 →", command=self.frontend).pack(side="left", padx=5)
        ttk.Button(actions, text="导出结果 CSV", command=self.export_csv).pack(side="right", padx=8)
        self.summary = ttk.Label(main, text="执行完整脚本；SQL 以英文分号结尾。", foreground=MUTED, wraplength=500)
        self.summary.pack(anchor="w", pady=4)
        self.result_select = ttk.Combobox(main, state="readonly")
        self.result_select.pack(fill="x", padx=(0, 8), pady=4)
        self.result_select.bind("<<ComboboxSelected>>", self.select_result)
        self.result_tree = table(main, ["结果"], height=8)
        ttk.Label(tracing, text="执行链路 / 选择阶段查看", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=6)
        self.stage_tree = table(tracing, ["阶段", "说明"], height=7)
        self.stage_tree.bind("<<TreeviewSelect>>", self.select_stage)
        self.trace_text = text_box(tracing, height=16)
        self.trace_text.pack(fill="both", expand=True, pady=(8, 0))

    def build_frontend(self):
        row = ttk.Frame(self.front_tab)
        row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text="分析 SQL 编辑器中的内容 · 不写数据库", foreground=MUTED).pack(side="left", padx=(0, 20))
        self.extensions = {}
        for feature in ["update", "order_limit", "distinct"]:
            variable = tk.BooleanVar(value=True)
            self.extensions[feature] = variable
            ttk.Checkbutton(row, text=feature, variable=variable).pack(side="left", padx=5)
        ttk.Button(row, text="重新分析", style="Accent.TButton", command=self.frontend).pack(side="right")
        panes = ttk.Panedwindow(self.front_tab, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left, right = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=1)
        ttk.Label(left, text="Token / 类型、原文与位置").pack(anchor="w", pady=8)
        self.token_tree = table(left, ["类型", "原文", "值", "行:列"])
        ttk.Label(right, text="AST / 抽象语法树 JSON").pack(anchor="w", pady=8)
        self.ast_text = text_box(right)
        self.ast_text.pack(fill="both", expand=True)
        self.front_note = ttk.Label(self.front_tab, text="UPDATE / ORDER BY / DISTINCT 当前仅支持解析展示。", foreground=MUTED)
        self.front_note.pack(anchor="w", pady=8)

    def build_storage(self):
        row = ttk.Frame(self.store_tab)
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="置换策略").pack(side="left")
        self.policy = ttk.Combobox(row, values=["lru", "fifo"], state="readonly", width=7)
        self.policy.set("lru")
        self.policy.pack(side="left", padx=8)
        ttk.Label(row, text="缓存容量（页）").pack(side="left")
        self.capacity = ttk.Spinbox(row, from_=1, to=1024, width=6)
        self.capacity.set(4)
        self.capacity.pack(side="left", padx=8)
        ttk.Button(row, text="应用并重连", command=self.apply_policy).pack(side="left")
        ttk.Button(row, text="刷新观测", command=lambda: self.submit("读取缓存快照…", self.session.snapshot, self.refresh)).pack(side="right")
        self.metrics = ttk.Label(self.store_tab, text="", font=("Microsoft YaHei UI", 12, "bold"))
        self.metrics.pack(anchor="w", pady=12)
        panels = ttk.Panedwindow(self.store_tab, orient="horizontal")
        panels.pack(fill="both", expand=True)
        left, right = ttk.Frame(panels), ttk.Frame(panels)
        panels.add(left, weight=1)
        panels.add(right, weight=2)
        ttk.Label(left, text="缓冲帧 / dirty 与 pin").pack(anchor="w", pady=5)
        self.frames = table(left, ["页号", "脏页", "Pin"], height=5)
        ttk.Label(left, text="最近 200 条真实缓冲池事件").pack(anchor="w", pady=8)
        self.events = table(left, ["动作", "页号", "详情"], height=8)
        row = ttk.Frame(right)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text="查看页号").pack(side="left")
        self.page_id = ttk.Spinbox(row, from_=0, to=0, width=7)
        self.page_id.set(0)
        self.page_id.pack(side="left", padx=7)
        ttk.Button(row, text="读取页面", command=self.read_page).pack(side="left")
        self.page_note = ttk.Label(right, wraplength=640)
        self.page_note.pack(fill="x", pady=8)
        self.page_canvas = tk.Canvas(right, height=65, bg="white", highlightthickness=0)
        self.page_canvas.pack(fill="x", pady=5)
        self.page_canvas.bind("<Configure>", lambda event: self.draw_page())
        self.current_page = None
        self.slots = table(right, ["槽号", "偏移", "字节数", "状态"], height=5)
        self.hex_text = text_box(right, height=9)
        self.hex_text.pack(fill="both", expand=True, pady=8)

    def build_rubric(self):
        """Build the scoring-document acceptance page.

        The page intentionally separates the immutable expected contract from
        the actual evidence returned by ``WorkbenchSession.run_rubric_case``.
        A reviewer can therefore select a storage scoring item, edit the
        Python operation recipe, run it, and inspect every assertion without
        changing the current workbench database.
        """

        controls = ttk.Frame(self.rubric_tab)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="预设存储脚本").pack(side="left")
        self.rubric_case = ttk.Combobox(controls, state="readonly", width=58)
        self.rubric_case.pack(side="left", padx=7)
        self.rubric_case.bind("<<ComboboxSelected>>", self.show_rubric_case)
        ttk.Button(controls, text="恢复预设代码", command=self.show_rubric_case).pack(side="left", padx=4)
        ttk.Button(controls, text="解析并运行 Python", style="Accent.TButton", command=self.run_rubric_case).pack(side="left", padx=4)
        self.rubric_status_var = tk.StringVar(value="请选择用例并运行")
        self.rubric_status_label = ttk.Label(controls, textvariable=self.rubric_status_var, foreground=MUTED)
        self.rubric_status_label.pack(side="right", padx=5)

        self.rubric_meta = ttk.Label(self.rubric_tab, text="", foreground=MUTED, wraplength=1250)
        self.rubric_meta.pack(fill="x", anchor="w", pady=(0, 8))
        panes = ttk.Panedwindow(self.rubric_tab, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(0, 8))
        left, right = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=1)
        self.rubric_source_label = ttk.Label(left, text="Python 存储验收代码（可编辑）", font=("Microsoft YaHei UI", 10, "bold"))
        self.rubric_source_label.pack(anchor="w", pady=5)
        self.rubric_source = text_box(left, height=13)
        self.rubric_source.pack(fill="both", expand=True)
        bind_python_highlighting(self.rubric_source)
        ttk.Label(right, text="预期结果（评分依据）", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=5)
        self.rubric_expected = text_box(right, height=8)
        self.rubric_expected.pack(fill="both", expand=True)
        ttk.Label(right, text="实际结果与证据", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(9, 5))
        self.rubric_actual = text_box(right, height=8)
        self.rubric_actual.pack(fill="both", expand=True)

        ttk.Label(self.rubric_tab, text="存储检查结果", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=4)
        self.rubric_effect = table(self.rubric_tab, ["结果"], height=4)
        ttk.Label(self.rubric_tab, text="逐项断言", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=4)
        self.rubric_checks = table(self.rubric_tab, ["状态", "验收项", "实际证据"], height=6)
        self._rubric_case_ids: list[str] = []
        self.update_rubric_cases()

    def _selected_rubric_case(self):
        index = self.rubric_case.current()
        if index < 0 or index >= len(self._rubric_case_ids):
            return None
        case_id = self._rubric_case_ids[index]
        return next(case for case in RUBRIC_CASES if case.case_id == case_id)

    def update_rubric_cases(self, event=None):
        """Load the three storage recipes into the single Python selector."""

        cases = list(RUBRIC_CASES)
        self._rubric_case_ids = [case.case_id for case in cases]
        self.rubric_case.configure(values=[f"{case.case_id}  ·  {case.title}" for case in cases])
        if cases:
            self.rubric_case.current(0)
            self.show_rubric_case()

    def show_rubric_case(self, event=None):
        case = self._selected_rubric_case()
        if case is None:
            return
        self.rubric_meta.configure(text=f"{case.case_id}  ·  {case.rubric}  ·  {case.purpose}")
        # Selecting a case restores its commented Python recipe.  The widget
        # remains editable after this call, and the next run reads it back.
        set_text(self.rubric_source, case.source, readonly=False)
        highlight_python(self.rubric_source)
        self.rubric_source_label.configure(text="Python 存储验收代码（可编辑，运行当前内容）")
        set_text(self.rubric_expected, case.expected)
        set_text(self.rubric_actual, "尚未运行。点击“运行用例”后，这里会显示真实执行证据。")
        self.rubric_effect.configure(columns=("c0",))
        self.rubric_effect.heading("c0", text="结果")
        self.rubric_effect.column("c0", width=520)
        fill(self.rubric_effect, [])
        fill(self.rubric_checks, [])
        self.rubric_status_var.set("尚未运行")
        self.rubric_status_label.configure(foreground=MUTED)

    def run_rubric_case(self):
        case = self._selected_rubric_case()
        if case is None:
            return
        source = self.rubric_source.get("1.0", "end-1c")
        if not source.strip():
            self.rubric_status_var.set("Python 代码为空，请先编辑左侧代码")
            return
        self.submit(
            f"解析并运行存储 Python {case.case_id}…",
            lambda: self.session.run_rubric_case(case.case_id, source=source),
            self.show_rubric_result,
        )

    def show_rubric_result(self, result):
        self.tabs.select(self.rubric_tab)
        state = "PASS" if result["passed"] else "FAIL"
        color = "#16704a" if result["passed"] else "#b91c1c"
        self.rubric_status_var.set(f"{state} · {result['case_id']}")
        self.rubric_status_label.configure(foreground=color)
        set_text(self.rubric_expected, result["expected"])
        set_text(self.rubric_actual, result["actual"])
        effect = result.get("effect") or {}
        columns = tuple(effect.get("columns") or ())
        rows = tuple(effect.get("rows") or ())
        if not columns:
            columns = ("消息", "影响行数")
            rows = ((effect.get("message") or "无结构化结果", effect.get("affected_rows", 0)),)
        ids = [f"r{i}" for i in range(len(columns))]
        self.rubric_effect.configure(columns=ids)
        for column_id, label in zip(ids, columns):
            self.rubric_effect.heading(column_id, text=str(label))
            self.rubric_effect.column(column_id, width=max(110, min(260, 720 // len(ids))))
        fill(self.rubric_effect, rows)
        fill(
            self.rubric_checks,
            [["PASS" if item["passed"] else "FAIL", item["name"], item["evidence"].replace("\n", " ")]
             for item in result["checks"]],
        )
        self.status.set(f"评分用例 {result['case_id']}：{state}；当前数据库未被用例修改。")

    def build_help(self):
        help_text = text_box(self.help_tab)
        help_text.pack(fill="both", expand=True)
        set_text(help_text, """MiniDB Studio 使用指南

01  从一个完整演示开始
在 SQL 工作台点击“完整演示（新库）”，程序创建独立数据库并执行真实的建表、插入与查询。
从结果下拉框选择任意语句，再点击右侧 TOKEN、AST、SEMANTIC、BOUND、PLAN、OPTIMIZATION、OPTIMIZED_PLAN、EXECUTION 阶段，查看真实输出。
“1000 行跨页演示”可以观察多个数据页和缓存淘汰。每次演示使用新文件，之前的数据保留。

02  逐步学习 SQL
示例 01–05 按顺序展示建表、中文插入、布尔条件、优化和删除。建表只能在同名表不存在时执行。
F5 或 Ctrl+Enter 执行编辑器中的完整脚本。SELECT 的结果展示在表格中；可导出当前语句的全部结果为 CSV。
双击左侧表名载入 SELECT。表下方显示字段和类型。
语法错误在任何执行前报错；后续语句的语义或执行错误可能保留前面已成功的语句，系统没有事务回滚。

03  前端扩展与错误
示例 08–10 配合“仅分析”展示 UPDATE、ORDER BY/LIMIT、DISTINCT 的 Token 和 AST。
这些扩展尚未在本下载版的核心执行链注册，不能使用“执行 SQL”将它们当成已支持的数据库操作。
“前端分析”开关可以验证同一 SQL 在开启或关闭扩展时的差别。示例 06/07 用于语法及语义错误定位。

03  存储与持久化
页与缓存页显示实时命中、缺失、淘汰、读写统计以及缓冲帧的 dirty/pin 状态。
读取页号可以检查真实的 4096 字节内容、槽目录、删除标记和可用空间；优先显示缓存中的最新页。
“刷盘”将脏页写入磁盘。“重连”关闭并重新打开数据库，再次 SELECT 可以验证数据恢复。
更改 LRU/FIFO 与容量后点击“应用并重连”；统计随新会话重新计数。观察快照本身不触碰缓存置换顺序。

04  Python 存储演示
“Python 存储演示”页只保留评分文档中存储系统的三类基本功能：页面管理、缓存机制、
接口与上层衔接。每个预设脚本都带有中文注释，说明它对应的页分配/释放、4096 字节
读写、LRU/FIFO、缓存事件和数据库接口检查。左侧代码框可以直接编辑，关键字、字符串、
数字、注释和运算符会以不同颜色显示；点击“解析并运行
Python”会在临时数据库中编译并执行当前源码，右侧显示源码自己的 print 标准输出、动态
检查结果和 PASS/FAIL。结果不会预先写死，也不会修改当前工作库；代码异常会显示实际异常。

05  文件与退出
默认工作库在仓库目录 data/workbench.db。打开数据库按钮只选择现有文件，新建按钮拒绝覆盖已有文件。
关闭界面会刷盘并关闭连接。任务运行期间请等待完成后退出。
运行入口：仓库根目录 run_gui.py。原来的纯前端演示保留在 run_frontend_gui.py。
""")

    def refresh(self, snapshot):
        self.snapshot = snapshot
        self.path_label.configure(text="●  " + snapshot["path"])
        self.policy.set(snapshot["policy"])
        self.capacity.set(snapshot["capacity"])
        self.catalog.delete(*self.catalog.get_children())
        for item in snapshot["tables"]:
            node = self.catalog.insert("", "end", text=item["name"], values=[item["name"]], open=True)
            for column in item["columns"]:
                dtype = getattr(column["dtype"], "value", column["dtype"])
                self.catalog.insert(node, "end", text=f'{column["name"]}   {dtype}', values=[item["name"]])
        stats = snapshot["stats"]
        self.metrics.configure(text=f'{snapshot["pages"]} 页  ·  {snapshot["pages"] * 4096:,} B 页空间  ·  '
                               f'命中 {stats["hits"]}   缺失 {stats["misses"]}   淘汰 {stats["evictions"]}   读 {stats["reads"]}   写 {stats["writes"]}')
        fill(self.frames, [[f["page"], "dirty" if f["dirty"] else "clean", f["pins"]] for f in snapshot["frames"]])
        fill(self.events, [[e["action"], e["page_id"], e["detail"]] for e in snapshot["events"]])
        self.page_id.configure(to=max(0, snapshot["pages"] - 1))
        self.current_page = None
        self.page_canvas.delete("all")
        self.page_note.configure(text="点击“读取页面”查看当前快照；数据更新后需要重新读取。")
        fill(self.slots, [])
        set_text(self.hex_text, "")

    def load_example(self):
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", EXAMPLES[self.example.get()])

    def table_query(self, event=None):
        selected = self.catalog.selection()
        if selected:
            name = self.catalog.item(selected[0], "values")[0]
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", f"SELECT * FROM {name};")

    def execute(self):
        source = self.editor.get("1.0", "end-1c")
        self.editor.tag_remove("error", "1.0", "end")
        self.submit("正在执行 SQL…", lambda: self.session.execute(source), self.show_execution)

    def show_execution(self, outcome):
        self.tabs.select(self.sql_tab)
        self.refresh(outcome["snapshot"])
        self.results = outcome["results"]
        self.result_select.configure(values=[f'{i+1:03}  ·  {r["message"] or "查询"}' for i, r in enumerate(self.results)])
        self.result_select.set("")
        fill(self.result_tree, [])
        fill(self.stage_tree, [])
        set_text(self.trace_text, "")
        if outcome["error"]:
            note = outcome["error"] + "\n执行已停止；若是后续语义/执行错误，前序语句可能已生效。"
            self.summary.configure(text=note, foreground="#b91c1c")
            set_text(self.trace_text, note)
            if outcome["position"] and self.editor.get("1.0", "end-1c") == outcome["source"]:
                line, column = outcome["position"]
                point = f"{line}.{column - 1}"
                self.editor.tag_add("error", point, point + "+1c")
                self.editor.see(point)
            self.status.set("SQL 执行失败，请查看错误及表结构。")
        else:
            self.summary.configure(text=f'{len(self.results)} 条语句执行成功 · {outcome["seconds"] * 1000:.1f} ms', foreground="#16704a")
            self.status.set("执行完成 · 结果与执行链路已更新")
            if self.results:
                self.result_select.current(len(self.results) - 1)
                self.select_result()

    def select_result(self, event=None):
        index = self.result_select.current()
        if index < 0:
            return
        result = self.results[index]
        columns = result["columns"] or ["执行消息", "影响行数"]
        # Unique internal column IDs also support repeated SELECT column names.
        ids = [f"c{i}" for i in range(len(columns))]
        self.result_tree.configure(columns=ids)
        for name, label in zip(ids, columns):
            self.result_tree.heading(name, text=label)
            self.result_tree.column(name, width=130)
        rows = result["rows"] if result["columns"] else [[result["message"], result["affected_rows"]]]
        fill(self.result_tree, rows)
        fill(self.stage_tree, [[e["stage"], e["detail"].replace("\n", " ")[:64]] for e in result["trace"]])
        if self.stage_tree.get_children():
            self.stage_tree.selection_set(self.stage_tree.get_children()[0])

    def select_stage(self, event=None):
        selection = self.stage_tree.selection()
        if selection and self.result_select.current() >= 0:
            index = self.stage_tree.index(selection[0])
            detail = self.results[self.result_select.current()]["trace"][index]["detail"]
            try:
                detail = pretty(json.loads(detail))
            except json.JSONDecodeError:
                detail = str(detail)
            set_text(self.trace_text, detail)

    def frontend(self):
        source = self.editor.get("1.0", "end-1c")
        extensions = [key for key, value in self.extensions.items() if value.get()]
        self.submit("分析 Token 与 AST…", lambda: analyze(source, extensions), self.show_frontend)

    def show_frontend(self, result):
        self.tabs.select(self.front_tab)
        tokens = result["tokens"]
        fill(self.token_tree, [[t["kind"], t.get("lexeme", ""), t.get("value"),
                              f'{t["span"]["start"]["line"]}:{t["span"]["start"]["column"]}'] for t in tokens])
        set_text(self.ast_text, pretty(result["ast"]))
        self.front_note.configure(text=result["error"] or f'解析成功 · {len(tokens)} 个 Token · {len(result["ast"])} 条语句 · 未执行 SQL',
                                  foreground="#b91c1c" if result["error"] else "#16704a")

    def demo(self, large):
        def show(outcome):
            self.show_execution(outcome)
            if not outcome["error"]:
                self.editor.delete("1.0", "end")
                self.editor.insert("1.0", "SELECT * FROM student;\n")
                self.status.set("演示完成 · 编辑器已载入查询语句，可按 F5 继续；演示结果可在下拉框回看。")
        self.submit("创建独立演示库并执行真实 SQL…", lambda: self.session.isolated_demo(large), show)

    def open_db(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(filetypes=[("MiniDB 数据库", "*.db"), ("所有文件", "*.*")])
        if path:
            self.submit("打开数据库…", lambda: self.session.reconnect(path), self.changed_database)

    def new_db(self):
        if self.busy:
            return
        path = filedialog.asksaveasfilename(defaultextension=".db", filetypes=[("MiniDB 数据库", "*.db")])
        if path:
            if Path(path).exists():
                messagebox.showerror("文件已存在", "请选择一个新文件名；打开现有文件请使用“打开数据库”。")
                return
            self.submit("创建数据库…", lambda: self.session.reconnect(path), self.changed_database)

    def changed_database(self, snapshot):
        self.refresh(snapshot)
        self.results = []
        self.result_select.configure(values=[])
        self.result_select.set("")
        fill(self.result_tree, [])
        fill(self.stage_tree, [])
        set_text(self.trace_text, "")
        self.summary.configure(text="数据库已连接。可执行 SELECT 检查已有数据。", foreground=MUTED)

    def flush(self):
        self.submit("写回脏页…", self.session.flush, self.refresh)

    def reopen(self):
        self.submit("关闭并重新打开数据库…", self.session.reconnect, self.changed_database)

    def apply_policy(self):
        try:
            capacity = int(self.capacity.get())
        except ValueError:
            messagebox.showerror("容量无效", "请输入 1–1024 的整数。")
            return
        policy = self.policy.get()
        self.submit("应用置换策略并重连…", lambda: self.session.reconnect(policy=policy, capacity=capacity), self.changed_database)

    def read_page(self):
        try:
            page_id = int(self.page_id.get())
        except ValueError:
            messagebox.showerror("页号无效", "请输入整数页号。")
            return
        self.submit("读取页面内容…", lambda: self.session.page(page_id), self.show_page)

    def show_page(self, result):
        self.current_page = result
        self.page_note.configure(text=f'页 {result["page"]} · {result["origin"]}\n{result["description"]}')
        set_text(self.hex_text, result["hex"])
        fill(self.slots, [[s["id"], s["offset"], s["length"], "已删除 / tombstone" if s["deleted"] else "存活"] for s in result["slots"]])
        self.draw_page()

    def draw_page(self):
        canvas = self.page_canvas
        canvas.delete("all")
        page = self.current_page
        if not page:
            return
        width = max(200, canvas.winfo_width()) - 20
        if "free_start" in page:
            zones = [(0, page["free_start"], BLUE), (page["free_start"], page["free_end"], "#dbe8f0"), (page["free_end"], 4096, "#10a981")]
        else:
            zones = [(0, 4096, BLUE)]
        for start, end, color in zones:
            canvas.create_rectangle(10 + start / 4096 * width, 8, 10 + end / 4096 * width, 34, fill=color, outline="")
        canvas.create_text(10, 48, text="0 B     蓝：页头/槽目录    浅灰：可用空间    绿：记录区（含已删除记录）", anchor="w", fill=MUTED, font=("Microsoft YaHei UI", 9))

    def import_sql(self):
        path = filedialog.askopenfilename(filetypes=[("SQL", "*.sql"), ("所有文件", "*.*")])
        if path:
            try:
                source = Path(path).read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as error:
                messagebox.showerror("读取失败", str(error))
                return
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", source)

    def save_sql(self):
        path = filedialog.asksaveasfilename(defaultextension=".sql", filetypes=[("SQL", "*.sql")])
        if path:
            try:
                Path(path).write_text(self.editor.get("1.0", "end-1c"), encoding="utf-8")
            except OSError as error:
                messagebox.showerror("保存失败", str(error))

    def export_csv(self):
        index = self.result_select.current()
        if index < 0 or not self.results[index]["columns"]:
            self.status.set("请先选择一个查询结果。")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            try:
                with open(path, "w", encoding="utf-8-sig", newline="") as file:
                    writer = csv.writer(file)
                    writer.writerow(self.results[index]["columns"])
                    writer.writerows(self.results[index]["rows"])
            except OSError as error:
                messagebox.showerror("导出失败", str(error))
            else:
                self.status.set("结果已导出：" + path)

    def close(self):
        if self.busy:
            self.status.set("正在执行任务，请等待完成后关闭。")
            return
        try:
            self.session.close()
        except (MiniDBError, OSError, RuntimeError) as error:
            messagebox.showerror("关闭失败", str(error))
            return
        self.root.after_cancel(self.poll_id)
        self.executor.shutdown(wait=False)
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        session = WorkbenchSession(SOURCE / "data" / "workbench.db")
    except (MiniDBError, OSError, ValueError, RuntimeError) as error:
        messagebox.showerror("数据库启动失败", str(error), parent=root)
        root.destroy()
        return 1
    Workbench(root, session)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
