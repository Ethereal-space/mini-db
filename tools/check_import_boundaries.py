"""用 AST 检查四个成员包的跨模块导入边界与禁用依赖。"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


MEMBER_PACKAGES = frozenset({"frontend", "compiler", "storage", "runtime"})
BANNED_IMPORTS = frozenset({"sqlite3", "sqlalchemy", "lark", "ply"})


def _top_module(name: str) -> str:
    return name.partition(".")[0]


def _minidb_subpackage(name: str) -> str | None:
    parts = name.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "minidb" else None


def check_import_boundaries(repo_root: Path) -> list[str]:
    problems: list[str] = []
    package_root = repo_root / "minidb"
    for path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(repo_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (UnicodeDecodeError, SyntaxError) as error:
            problems.append(f"{relative}: 无法分析：{error}")
            continue
        path_parts = path.relative_to(package_root).parts
        owner = path_parts[0] if path_parts and path_parts[0] in MEMBER_PACKAGES else None
        for node in ast.walk(tree):
            names: list[tuple[str, int]] = []
            if isinstance(node, ast.Import):
                names.extend((alias.name, node.lineno) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append((node.module, node.lineno))
            for imported, line in names:
                top = _top_module(imported).casefold()
                if top in BANNED_IMPORTS:
                    problems.append(f"{relative}:{line}: 禁止依赖 {top}")
                if top == "tests":
                    problems.append(f"{relative}:{line}: 生产代码不能导入 tests")
                target = _minidb_subpackage(imported)
                if owner and target in MEMBER_PACKAGES and target != owner:
                    problems.append(
                        f"{relative}:{line}: {owner} 不能直接导入成员包 {target}，请使用 minidb.contracts"
                    )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    problems = check_import_boundaries(args.repo_root.resolve())
    if problems:
        print("导入边界检查失败：")
        print("\n".join(f"- {problem}" for problem in problems))
        return 1
    print("导入边界检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
