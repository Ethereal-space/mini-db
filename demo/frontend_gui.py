"""MiniDB 成员 A 的桌面图形演示。

运行：python -m demo.frontend_gui
"""

from dataclasses import dataclass
import json
import sys
import tkinter as tk
from tkinter import scrolledtext, ttk

from minidb.contracts.errors import MiniDBError
from minidb.frontend import Frontend


CORE_EXAMPLE = """CREATE TABLE student (id INT, name VARCHAR, age INT);
INSERT INTO student (id, name, age) VALUES (1, '张三', 18);
SELECT name, age FROM student WHERE age >= 18;
"""

EXTENSION_EXAMPLE = """UPDATE student SET age = 19 WHERE id = 1;
SELECT name FROM student WHERE age >= 18 ORDER BY name DESC LIMIT 10;
SELECT DISTINCT age FROM student;
"""


@dataclass(frozen=True)
class AnalysisResult:
    """与 Tk 控件无关的解析结果，便于自动测试和复用。"""

    ok: bool
    status: str
    token_text: str = ""
    ast_text: str = ""
    error_text: str = ""
    error_line: int | None = None
    error_column: int | None = None


def _pretty_json(text: str) -> str:
    """把前端产生的紧凑 JSON 展开，无法展开时保留原文。"""
    if not text:
        return ""
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, ValueError):
        return text


def analyze_sql(source: str, enabled_extensions: set[str] | None = None) -> AnalysisResult:
    """解析一段 SQL，返回可直接显示在窗口中的文本。"""
    if not source.strip():
        return AnalysisResult(False, "请输入 SQL 后再解析。", error_text="SQL 输入为空。")

    events = []
    try:
        statements = Frontend(
            events.append,
            enabled_extensions=enabled_extensions or set(),
        ).parse(source)
    except MiniDBError as error:
        traces = {event.stage: _pretty_json(event.detail) for event in events}
        line = error.span.start.line if error.span is not None else None
        column = error.span.start.column if error.span is not None else None
        return AnalysisResult(
            False,
            "解析失败，请查看错误信息。",
            token_text=traces.get("TOKEN", ""),
            error_text=str(error),
            error_line=line,
            error_column=column,
        )

    traces = {event.stage: _pretty_json(event.detail) for event in events}
    return AnalysisResult(
        True,
        f"解析成功：共 {len(statements)} 条 SQL 语句。",
        token_text=traces.get("TOKEN", ""),
        ast_text=traces.get("AST", ""),
    )


class FrontendGui:
    """SQL 输入、扩展开关以及 Token/AST 查看窗口。"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("MiniDB SQL 前端演示（成员 A）")
        self.root.geometry("1100x760")
        self.root.minsize(820, 580)

        self.extension_vars = {
            "update": tk.BooleanVar(value=False),
            "order_limit": tk.BooleanVar(value=False),
            "distinct": tk.BooleanVar(value=False),
        }
        self.status_var = tk.StringVar(value="请输入 SQL，按 F5 或 Ctrl+Enter 开始解析。")
        self._build_widgets()
        self._load_example(CORE_EXAMPLE, enable_extensions=False)

        self.root.bind("<F5>", self._parse_event)
        self.root.bind("<Control-Return>", self._parse_event)

    def _build_widgets(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 10))

        main = ttk.Frame(self.root, padding=14)
        main.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)

        ttk.Label(main, text="MiniDB SQL 前端演示", style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )

        input_box = ttk.LabelFrame(main, text="SQL 输入", padding=8)
        input_box.grid(row=1, column=0, sticky="nsew")
        input_box.columnconfigure(0, weight=1)
        input_box.rowconfigure(0, weight=1)
        self.sql_text = scrolledtext.ScrolledText(
            input_box,
            height=10,
            wrap=tk.NONE,
            undo=True,
            font=("Consolas", 11),
        )
        self.sql_text.grid(row=0, column=0, sticky="nsew")
        self.sql_text.tag_configure("parse_error", background="#ffd9d9")

        controls = ttk.Frame(main)
        controls.grid(row=2, column=0, sticky="ew", pady=8)
        ttk.Button(controls, text="解析 SQL", command=self.parse).pack(side=tk.LEFT)
        ttk.Button(
            controls,
            text="基础示例",
            command=lambda: self._load_example(CORE_EXAMPLE, False),
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            controls,
            text="拓展示例",
            command=lambda: self._load_example(EXTENSION_EXAMPLE, True),
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(controls, text="清空", command=self.clear).pack(side=tk.LEFT, padx=(8, 18))

        ttk.Label(controls, text="启用拓展：").pack(side=tk.LEFT)
        ttk.Checkbutton(controls, text="UPDATE", variable=self.extension_vars["update"]).pack(side=tk.LEFT)
        ttk.Checkbutton(
            controls, text="ORDER BY / LIMIT", variable=self.extension_vars["order_limit"]
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(controls, text="DISTINCT", variable=self.extension_vars["distinct"]).pack(side=tk.LEFT)

        self.notebook = ttk.Notebook(main)
        self.notebook.grid(row=3, column=0, sticky="nsew")
        self.outputs = {}
        for key, title in (("token", "Token"), ("ast", "AST"), ("error", "错误信息")):
            output = scrolledtext.ScrolledText(
                self.notebook,
                wrap=tk.NONE,
                font=("Consolas", 10),
                state=tk.DISABLED,
            )
            self.notebook.add(output, text=title)
            self.outputs[key] = output

        ttk.Label(main, textvariable=self.status_var, style="Status.TLabel").grid(
            row=4, column=0, sticky="w", pady=(8, 0)
        )

    def _set_output(self, name: str, value: str) -> None:
        widget = self.outputs[name]
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        widget.configure(state=tk.DISABLED)

    def _load_example(self, source: str, enable_extensions: bool) -> None:
        self.sql_text.delete("1.0", tk.END)
        self.sql_text.insert("1.0", source)
        for variable in self.extension_vars.values():
            variable.set(enable_extensions)
        self.sql_text.focus_set()

    def _parse_event(self, _event: tk.Event) -> str:
        self.parse()
        return "break"

    def parse(self) -> None:
        self.sql_text.tag_remove("parse_error", "1.0", tk.END)
        enabled = {name for name, variable in self.extension_vars.items() if variable.get()}
        result = analyze_sql(self.sql_text.get("1.0", "end-1c"), enabled)

        self._set_output("token", result.token_text)
        self._set_output("ast", result.ast_text)
        self._set_output("error", result.error_text)
        self.status_var.set(result.status)

        if result.ok:
            self.notebook.select(self.outputs["ast"])
            return

        self.notebook.select(self.outputs["error"])
        if result.error_line is not None and result.error_column is not None:
            start = f"{result.error_line}.{max(result.error_column - 1, 0)}"
            self.sql_text.tag_add("parse_error", start, f"{start}+1c")
            self.sql_text.see(start)
            self.sql_text.mark_set(tk.INSERT, start)
            self.sql_text.focus_set()

    def clear(self) -> None:
        self.sql_text.delete("1.0", tk.END)
        for name in self.outputs:
            self._set_output(name, "")
        self.status_var.set("请输入 SQL，按 F5 或 Ctrl+Enter 开始解析。")
        self.sql_text.focus_set()


def main() -> int:
    try:
        root = tk.Tk()
    except tk.TclError as error:
        print(f"无法创建图形窗口：{error}", file=sys.stderr)
        return 1
    FrontendGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
