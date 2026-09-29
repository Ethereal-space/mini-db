"""运行成员 A 验收并保存真实证据；不重生成契约哈希。"""

from pathlib import Path
import os
import subprocess
import sys

from record_delivery import record_delivery


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    destination = ROOT / "delivery"
    destination.mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    checks = [
        (["--version"], 0),
        (["-m", "pip", "--version"], 0),
        (["-m", "pytest", "--version"], 0),
        (["-m", "compileall", "-q", "minidb"], 0),
        (["-m", "pytest", "-q", "tests/contracts", "tests/frontend",
          "--junitxml=delivery/test-results.xml"], 0),
        (["-m", "demo.frontend_demo", "--file", "demo/core.sql"], 0),
        (["-m", "demo.frontend_demo", "--file", "demo/extensions.sql", "--enable", "update",
          "--enable", "order_limit", "--enable", "distinct"], 0),
        (["-m", "demo.frontend_demo", "--file", "demo/extensions.sql"], 1),
        (["-m", "demo.frontend_demo", "--file", "demo/errors.sql"], 1),
    ]
    failed = False
    records = []
    for arguments, expected in checks:
        run = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=env,
                             capture_output=True, text=True, encoding="utf-8")
        matched = run.returncode == expected
        version_expectations = {
            ("--version",): "Python 3.14.2",
            ("-m", "pip", "--version"): "pip 25.3 ",
            ("-m", "pytest", "--version"): "pytest 9.1.1",
        }
        version_prefix = version_expectations.get(tuple(arguments))
        if version_prefix is not None:
            matched = matched and run.stdout.startswith(version_prefix)
        failed |= not matched
        label = "PASS" if matched else "FAIL"
        command = "python " + " ".join(arguments)
        print(f"{label} {command} (exit={run.returncode}, expected={expected})")
        records.append(f"$ {command}\nexit={run.returncode}, expected={expected}\n"
                       f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}\n")
        if not matched:
            print(run.stdout)
            print(run.stderr)
    (destination / "validation.txt").write_text("\n".join(records), encoding="utf-8")
    if not failed:
        record_delivery(ROOT)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
