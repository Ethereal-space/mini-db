"""生成或校验冻结契约目录的跨平台 SHA-256 摘要。"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


HASH_FILE = "contracts.sha256"
CONTRACT_DIRECTORY = Path("minidb/contracts")


def contract_files(repo_root: Path) -> list[Path]:
    """返回参与契约摘要的 Python 文件，顺序与操作系统无关。"""

    directory = repo_root / CONTRACT_DIRECTORY
    return sorted(
        (path for path in directory.rglob("*.py") if "__pycache__" not in path.parts),
        key=lambda path: path.relative_to(repo_root).as_posix(),
    )


def compute_contract_hash(repo_root: Path) -> str:
    """计算路径和内容共同决定的规范摘要。"""

    files = contract_files(repo_root)
    if not files:
        raise FileNotFoundError(f"没有找到冻结契约文件：{repo_root / CONTRACT_DIRECTORY}")
    digest = hashlib.sha256(b"MiniDB frozen contracts schema 1\0")
    for path in files:
        relative = path.relative_to(repo_root).as_posix().encode("utf-8")
        content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def read_recorded_hash(path: Path) -> str:
    """读取纯摘要或 sha256sum 风格的首字段。"""

    value = path.read_text(encoding="utf-8").strip().split()
    if not value or len(value[0]) != 64:
        raise ValueError(f"{path} 不含有效 SHA-256 摘要")
    return value[0].casefold()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="仓库根目录",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="与 contracts.sha256 比较")
    mode.add_argument("--write", action="store_true", help="更新 contracts.sha256")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    actual = compute_contract_hash(repo_root)
    record = repo_root / HASH_FILE
    if args.write:
        record.write_text(f"{actual}\n", encoding="utf-8", newline="\n")
        print(f"已写入 {record}: {actual}")
        return 0
    if args.check:
        try:
            expected = read_recorded_hash(record)
        except (OSError, ValueError) as error:
            print(f"契约摘要校验失败：{error}")
            return 1
        if actual != expected:
            print(f"契约摘要不匹配：期望 {expected}，实际 {actual}")
            return 1
        print(f"契约摘要一致：{actual}")
        return 0
    print(actual)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
