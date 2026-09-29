"""Portable launcher for the MiniDB CLI and GUI (Python 3.14)."""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def launch(module, arguments):
    candidates = [ROOT / '.venv' / 'Scripts' / 'python.exe', ROOT / '.venv' / 'bin' / 'python']
    python = next((path for path in candidates if path.is_file()), Path(sys.executable))
    version = subprocess.run([str(python), '-c', 'import sys; print(str(sys.version_info.major) + "." + str(sys.version_info.minor))'],
                             capture_output=True, text=True)
    if version.returncode or version.stdout.strip() != '3.14':
        print('MiniDB requires Python 3.14. Create .venv with Python 3.14, then run this file again.', file=sys.stderr)
        return 1
    environment = os.environ.copy()
    environment['PYTHONUTF8'] = '1'
    environment['PYTHONIOENCODING'] = 'utf-8'
    return subprocess.call([str(python), '-X', 'utf8', '-m', module, *arguments], cwd=ROOT, env=environment)


if __name__ == '__main__':
    raise SystemExit(launch('minidb', sys.argv[1:] or ['--interactive']))
