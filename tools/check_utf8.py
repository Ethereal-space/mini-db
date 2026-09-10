"""检查仓库文本能以严格 UTF-8 解码且不含替换字符。"""

from __future__ import annotations

import argparse
from pathlib import Path


TEXT_SUFFIXES = {
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".gitignore",
    ".gitattributes",
}
TEXT_NAMES = {"AGENTS.md", "CLAUDE.md", ".python-version", ".gitignore", ".gitattributes"}
SKIP_DIRECTORIES = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "_integration_input",
    "_integration_output",
    "rendered",
}


def text_files(repo_root: Path) -> list[Path]:
    result: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        if path.name in TEXT_NAMES or path.suffix.casefold() in TEXT_SUFFIXES:
            result.append(path)
    return sorted(result, key=lambda path: path.relative_to(repo_root).as_posix())


def check_utf8(repo_root: Path) -> list[str]:
    problems: list[str] = []
    for path in text_files(repo_root):
        relative = path.relative_to(repo_root).as_posix()
        try:
            content = path.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError as error:
            problems.append(f"{relative}: 非 UTF-8 字节，位置 {error.start}")
            continue
        if "\ufffd" in content:
            problems.append(f"{relative}: 包含 Unicode 替换字符 U+FFFD")
        if "\x00" in content:
            problems.append(f"{relative}: 文本文件包含 NUL 字符")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    problems = check_utf8(root)
    if problems:
        print("UTF-8 检查失败：")
        print("\n".join(f"- {problem}" for problem in problems))
        return 1
    print(f"UTF-8 检查通过：{len(text_files(root))} 个文本文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
