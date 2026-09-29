from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools.generate_contract_hash import compute_contract_hash


def test_contract_hash_is_stable_across_line_endings(tmp_path: Path) -> None:
    first = tmp_path / "first" / "minidb" / "contracts"
    second = tmp_path / "second" / "minidb" / "contracts"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "a.py").write_bytes(b"x = 1\r\n")
    (second / "a.py").write_bytes(b"x = 1\n")
    assert compute_contract_hash(tmp_path / "first") == compute_contract_hash(tmp_path / "second")


def test_contract_hash_changes_with_path_or_content(tmp_path: Path) -> None:
    contracts = tmp_path / "minidb" / "contracts"
    contracts.mkdir(parents=True)
    source = contracts / "a.py"
    source.write_text("x = 1\n", encoding="utf-8")
    before = compute_contract_hash(tmp_path)
    source.write_text("x = 2\n", encoding="utf-8")
    assert compute_contract_hash(tmp_path) != before


def test_preflight_tool_supports_direct_script_invocation() -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, str(root / "tools/validate_integration_inputs.py"), "--help"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 0
    assert b"--input-root" in completed.stdout
