"""运行 python -m demo.frontend_demo --file demo/core.sql。"""

import argparse
from pathlib import Path
import sys

from minidb.contracts.errors import MiniDBError
from minidb.frontend import Frontend
from minidb.frontend.parser import SUPPORTED_EXTENSIONS


def main() -> int:
    parser = argparse.ArgumentParser(description="MiniDB 成员 A：仅展示 Token 与 AST")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--file", type=Path, help="UTF-8 SQL 文件")
    group.add_argument("--sql", help="直接传入 SQL；两者均省略时读取 stdin 至 EOF")
    parser.add_argument("--enable", action="append", default=[], choices=sorted(SUPPORTED_EXTENSIONS),
                        help="启用一项前端扩展；可重复使用。默认仅核心 SQL")
    args = parser.parse_args()
    try:
        if args.file is not None:
            source = args.file.read_text(encoding="utf-8")
        elif args.sql is not None:
            source = args.sql
        else:
            source = sys.stdin.read()
        events = []
        statements = Frontend(events.append, enabled_extensions=args.enable).parse(source)
    except (MiniDBError, OSError, UnicodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"解析成功：{len(statements)} 条语句。此演示只生成前端结构，不执行数据库操作。")
    for event in events:
        print(f"\n{event.stage}\n{event.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
