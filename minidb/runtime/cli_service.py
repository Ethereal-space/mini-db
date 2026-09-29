"""通过可注入 ApplicationPort 工厂驱动的命令行服务。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TextIO

from minidb.contracts import ApplicationPort, MiniDBError

from .result_formatter import format_error, format_result


AppFactory = Callable[[str, str, int], AbstractContextManager[ApplicationPort]]


class _ParserExit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


class _ArgumentParser(argparse.ArgumentParser):
    def __init__(self, stdout: TextIO, stderr: TextIO) -> None:
        self._stdout = stdout
        self._stderr = stderr
        super().__init__(prog="minidb", description="教学型页式 MiniDB")

    def print_help(self, file: TextIO | None = None) -> None:
        super().print_help(file or self._stdout)

    def error(self, message: str) -> None:
        self.print_usage(self._stderr)
        self._stderr.write(f"{self.prog}: 参数错误: {message}\n")
        raise _ParserExit(2)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            (self._stderr if status else self._stdout).write(message)
        raise _ParserExit(status)


def _build_parser(stdout: TextIO, stderr: TextIO) -> _ArgumentParser:
    parser = _ArgumentParser(stdout, stderr)
    parser.add_argument("--db", default="mini.db", help="数据库文件路径")
    parser.add_argument("--file", help="UTF-8 SQL 文件；省略时读取标准输入")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="进入交互式 SQL 模式；省略 --file 时终端也会自动进入",
    )
    parser.add_argument("--encoding", default="utf-8", help="SQL 文件编码，默认 utf-8")
    parser.add_argument("--trace", action="store_true", help="显示执行跟踪")
    parser.add_argument("--policy", choices=("lru", "fifo"), default="lru", help="页面替换策略")
    parser.add_argument("--capacity", type=int, default=16, help="缓冲池页数，必须为正整数")
    return parser


def _first_terminator(source: str) -> int | None:
    """返回字符串中第一个 SQL 分号的位置，忽略字符串和注释内容。"""

    in_string = False
    in_line_comment = False
    in_block_comment = False
    index = 0
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if in_line_comment:
            if char in "\r\n":
                in_line_comment = False
            index += 1
            continue
        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
            else:
                index += 1
            continue
        if in_string:
            if char == "'":
                if next_char == "'":
                    index += 2
                    continue
                in_string = False
            index += 1
            continue
        if char == "'":
            in_string = True
            index += 1
        elif char == "-" and next_char == "-":
            in_line_comment = True
            index += 2
        elif char == "/" and next_char == "*":
            in_block_comment = True
            index += 2
        elif char == ";":
            return index
        else:
            index += 1
    return None


def _is_terminal(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    return bool(isatty()) if callable(isatty) else False


def _print_interactive_help(stdout: TextIO) -> None:
    stdout.write("MiniDB 交互模式：输入以分号结束的 SQL，或使用 .help、.quit、.exit。\n")
    stdout.write("支持 CREATE TABLE、INSERT、SELECT、DELETE；输入多行 SQL 时最后一行加分号。\n")
    stdout.flush()


def _run_interactive(
    app: ApplicationPort,
    *,
    trace: bool,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """持续读取 SQL；每遇到完整分号就执行并在错误后继续等待输入。"""

    buffer = ""
    while True:
        stdout.write("minidb> " if not buffer.strip() else "...> ")
        stdout.flush()
        line = stdin.readline()
        if line == "":
            if buffer.strip():
                try:
                    results = app.execute(buffer, trace)
                    for result in results:
                        stdout.write(format_result(result) + "\n")
                except MiniDBError as error:
                    stderr.write(format_error(error) + "\n")
            stdout.write("再见。\n")
            stdout.flush()
            return 0

        stripped = line.strip()
        if not buffer.strip() and stripped.casefold() in {".quit", ".exit"}:
            stdout.write("再见。\n")
            stdout.flush()
            return 0
        if not buffer.strip() and stripped.casefold() == ".help":
            _print_interactive_help(stdout)
            continue
        buffer += line
        while True:
            terminator = _first_terminator(buffer)
            if terminator is None:
                break
            statement = buffer[: terminator + 1].strip()
            buffer = buffer[terminator + 1 :]
            if not statement.strip():
                continue
            try:
                results = app.execute(statement, trace)
                for result in results:
                    stdout.write(format_result(result) + "\n")
                stdout.flush()
            except MiniDBError as error:
                stderr.write(format_error(error) + "\n")
                stderr.flush()


def main(
    argv: Sequence[str] | None,
    app_factory: AppFactory,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """读取完整 SQL 源文本并交给 ApplicationPort；返回稳定退出码。"""

    parser = _build_parser(stdout, stderr)
    try:
        args = parser.parse_args(argv)
        if args.capacity <= 0:
            parser.error("--capacity 必须为正整数")
        if args.interactive and args.file is not None:
            parser.error("--interactive 不能与 --file 同时使用")
    except _ParserExit as error:
        return error.status

    interactive = args.interactive or (args.file is None and _is_terminal(stdin))

    if interactive:
        try:
            with app_factory(args.db, args.policy, args.capacity) as app:
                return _run_interactive(
                    app,
                    trace=args.trace,
                    stdin=stdin,
                    stdout=stdout,
                    stderr=stderr,
                )
        except MiniDBError as error:
            stderr.write(format_error(error) + "\n")
            return 1
        except OSError as error:
            stderr.write(f"[IO/CLI_IO] {error}\n")
            return 1

    try:
        if args.file is None:
            source = stdin.read()
        else:
            source = Path(args.file).read_text(encoding=args.encoding)
    except (OSError, UnicodeError, LookupError) as error:
        stderr.write(f"读取 SQL 源失败: {error}\n")
        return 1

    try:
        with app_factory(args.db, args.policy, args.capacity) as app:
            results = app.execute(source, args.trace)
        for index, result in enumerate(results):
            if index:
                stdout.write("\n")
            stdout.write(format_result(result))
            stdout.write("\n")
        return 0
    except MiniDBError as error:
        stderr.write(format_error(error) + "\n")
        return 1
    except OSError as error:
        stderr.write(f"[IO/CLI_IO] {error}\n")
        return 1


__all__ = ["AppFactory", "main"]
