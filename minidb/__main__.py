"""MiniDB 命令行入口。"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from minidb.integration.app import open_application
from minidb.runtime.cli_service import main as cli_main


def main(argv: Sequence[str] | None = None) -> int:
    return cli_main(argv, open_application)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
