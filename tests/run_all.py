"""Run every test suite: Python (tests/backend, tests/database) and frontend
(tests/frontend via vitest). Exit status is non-zero if any suite fails.

    python tests/run_all.py
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend-next"
NPX = "npx.cmd" if sys.platform == "win32" else "npx"

suites = [
    ("backend + database (pytest)", [sys.executable, "-m", "pytest", str(REPO / "tests")], REPO),
    ("frontend (vitest)", [NPX, "vitest", "run"], FRONTEND),
]

failed = []
for name, cmd, cwd in suites:
    print(f"\n=== {name} ===", flush=True)
    if subprocess.run(cmd, cwd=cwd).returncode != 0:
        failed.append(name)

print("\n" + ("ALL SUITES PASSED" if not failed else "FAILED: " + ", ".join(failed)))
sys.exit(1 if failed else 0)
