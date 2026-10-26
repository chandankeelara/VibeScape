"""
Start and stop a local uvicorn on a DJ-eval copy.

    with LocalServer(Path("data/dj_eval/eval.db")) as srv:
        ...  # srv.base_url

No `--reload`: its supervisor hands the listening socket to a child, and if
the parent dies the port keeps serving from an orphan (root CLAUDE.md). One
process, terminated on exit, is the whole lifecycle.

The environment is built, not inherited wholesale: DB_BACKEND is forced to
sqlite and every TURSO_* variable is dropped, so nothing in the caller's
shell can point this server at production. backend/bootstrap.py also calls
load_dotenv(<repo>/.env), which never overrides a variable already set.
VIBESCAPE_DB_PATH is the only way to choose the file (backend/CLAUDE.md:
setting db.DB_PATH does nothing).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests

_REPO = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalServer:
    def __init__(self, db_path: Path, port: int | None = None,
                 python: str = sys.executable, log_path: Path | None = None,
                 extra_env: dict | None = None):
        self.db_path = Path(db_path).resolve()
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)
        self.port = port or _free_port()
        self.python = python
        self.log_path = log_path or self.db_path.with_name(self.db_path.name + ".uvicorn.log")
        self.extra_env = extra_env or {}
        self.proc: subprocess.Popen | None = None
        self._log = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith("TURSO_")}
        env["DB_BACKEND"] = "sqlite"
        env["VIBESCAPE_DB_PATH"] = str(self.db_path)
        env.pop("DB_PATH", None)
        env.update(self.extra_env)
        return env

    def start(self, timeout: float = 90.0) -> "LocalServer":
        self._log = open(self.log_path, "ab")
        self.proc = subprocess.Popen(
            [self.python, "-m", "uvicorn", "app:app", "--app-dir", str(_REPO / "backend"),
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=str(_REPO), env=self._env(), stdout=self._log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"uvicorn exited with {self.proc.returncode}; see {self.log_path}")
            try:
                r = requests.get(self.base_url + "/api/health", timeout=2)
                if r.ok:
                    return self
            except requests.RequestException:
                pass
            time.sleep(0.3)
        self.stop()
        raise TimeoutError(f"uvicorn not ready after {timeout}s; see {self.log_path}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=15)
        self.proc = None
        if self._log:
            self._log.close()
            self._log = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
