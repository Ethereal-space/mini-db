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
    parser.add_argument("--encoding", default="utf-8", help="SQL 文件编码，默认 utf-8")
    parser.add_argument("--trace", action="store_true", help="显示执行跟踪")
    parser.add_argument("--policy", choices=("lru", "fifo"), default="lru", help="页面替换策略")
    parser.add_argument("--capacity", type=int, default=16, help="缓冲池页数，必须为正整数")
    return parser


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
    except _ParserExit as error:
        return error.status

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
