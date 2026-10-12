"""Create user_embedding_mean on Turso.

Why this exists: backend/db.py's ensure_db() returns immediately when
DB_BACKEND is turso/libsql, so schema.sql never reaches production. Until
this has run, DJ replay still works — _dj_library_mean logs a warning and
keeps the mean in process memory — but every cold Cloud Run instance then
re-reads the whole library's vectors on its first DJ request per user.

Idempotent (CREATE ... IF NOT EXISTS), DDL read straight out of schema.sql.

Credentials come from the environment, NOT from scripts/_load_gcp_secrets.ps1
(the repo rule is to never widen what reads that file). Set them in your own
shell first, then:

    $env:DB_BACKEND="turso"; $env:TURSO_DATABASE_URL=...; $env:TURSO_AUTH_TOKEN=...
    python scripts/_turso_create_user_embedding_mean.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TABLE = "user_embedding_mean"


def _statement() -> str:
    for chunk in (_REPO / "schema.sql").read_text(encoding="utf-8").split(";"):
        body = "\n".join(ln for ln in chunk.splitlines()
                         if not ln.strip().startswith("--")).strip()
        head = body.upper().find("CREATE")
        if head >= 0 and f"EXISTS {_TABLE}" in body:
            return body[head:]
    raise SystemExit(f"{_TABLE} not found in schema.sql")


def main() -> int:
    if (os.environ.get("DB_BACKEND") or "").lower() not in ("turso", "libsql"):
        print("DB_BACKEND is not turso — refusing (this script is for production only).")
        return 1
    for k in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        if not os.environ.get(k):
            print(f"{k} is not set.")
            return 1
    sys.path.insert(0, str(_REPO / "backend"))
    import db_client  # noqa: E402

    c = db_client.create_connection()
    c.execute(_statement())
    c.commit()
    n = c.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0]
    print(f"{_TABLE}: {n} row(s). done.")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
