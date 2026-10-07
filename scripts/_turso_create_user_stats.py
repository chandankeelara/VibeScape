"""Create the per-user cadence table on Turso: user_stats.

Why this exists: backend/db.py's ensure_db() returns immediately when
DB_BACKEND is turso/libsql, so schema.sql is never applied to production.
Local SQLite gets this table from schema.sql automatically; Turso needs
this one-shot script. Run it before deploying the DJ per-user half-life
change -- until it has run, _fetch_user_halflife logs
"no such table: user_stats" per request and silently falls back to the
fixed DJ_RECENCY_HALFLIFE_H env default (which is the pre-feature behaviour
and perfectly safe, just not actually personalised yet).

Idempotent -- the statement is CREATE ... IF NOT EXISTS, and the DDL is
read straight out of schema.sql so the two cannot drift.

    python scripts/_turso_create_user_stats.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
_PS1 = _REPO / "scripts" / "_load_gcp_secrets.ps1"
_SCHEMA = _REPO / "schema.sql"

_TABLE = "user_stats"


def _statements() -> list[str]:
    """Pull the CREATE statement for user_stats out of schema.sql.

    Same comment-stripping / first-CREATE slicing as
    _turso_create_event_tables.py -- schema.sql's prose comments contain
    semicolons, so a split on ';' lands chunks that open mid-sentence.
    """
    out = []
    for chunk in _SCHEMA.read_text(encoding="utf-8").split(";"):
        body = "\n".join(
            ln for ln in chunk.splitlines() if not ln.strip().startswith("--")
        ).strip()
        head = body.upper().find("CREATE")
        if head < 0:
            continue
        body = body[head:]
        if _TABLE in body:
            out.append(body)
    return out


def main() -> int:
    stmts = _statements()
    # Just the one table today. If this trips, schema.sql moved and the
    # extraction above needs revisiting -- better to stop than to half-apply.
    if len(stmts) != 1:
        print(f"ERROR: expected 1 statement in schema.sql, found {len(stmts)}")
        for s in stmts:
            print("  --", s.splitlines()[0])
        return 1

    text = _PS1.read_text(encoding="utf-8")
    os.environ["TURSO_DATABASE_URL"] = re.search(r'\$turso_url\s*=\s*"([^"]+)"', text).group(1)
    os.environ["TURSO_AUTH_TOKEN"] = re.search(r'\$turso_token\s*=\s*"([^"]+)"', text).group(1)
    os.environ["DB_BACKEND"] = "turso"
    sys.path.insert(0, str(_REPO / "backend"))
    import db_client  # noqa: E402

    print("connecting to Turso ...")
    c = db_client.create_connection()
    for s in stmts:
        head = " ".join(s.split())[:90]
        print(f"  {head} ...")
        c.execute(s)
    c.commit()

    n = c.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0]
    print(f"{_TABLE}: {n} row(s)")
    c.close()
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
