"""Create the listening-telemetry tables on Turso: track_events and
user_track_stats (plus their indexes).

Why this exists: backend/db.py's ensure_db() returns immediately when
DB_BACKEND is turso/libsql, so schema.sql is never applied to production.
Local SQLite gets these tables from schema.sql automatically; Turso needs
this one-shot script. Run it before deploying anything that posts to
/api/events — until it has run, POST /api/events logs "no such table" and
answers 202 with everything in `rejected` (by design: telemetry never
surfaces an error to the client).

Idempotent — every statement is CREATE ... IF NOT EXISTS, and the DDL is
read straight out of schema.sql so the two cannot drift.

    python scripts/_turso_create_event_tables.py
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

_TABLES = ("track_events", "user_track_stats")


def _statements() -> list[str]:
    """Pull the CREATE statements for the telemetry tables out of schema.sql.

    Splitting on ';' is safe for the statements themselves — none of them
    contains a semicolon inside a string or a trigger body — but schema.sql's
    prose comments do contain semicolons, so a chunk can open mid-sentence.
    Hence the slice to the first CREATE after comment lines are dropped.
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
        if any(t in body for t in _TABLES):
            out.append(body)
    return out


def main() -> int:
    stmts = _statements()
    # 2 tables + 3 indexes. If this trips, schema.sql moved and the extraction
    # above needs revisiting — better to stop than to half-apply the schema.
    if len(stmts) != 5:
        print(f"ERROR: expected 5 statements in schema.sql, found {len(stmts)}")
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

    for t in _TABLES:
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        idx = c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name = ?", (t,)
        ).fetchall()
        print(f"{t}: {n} row(s), indexes: {[r[0] for r in idx]}")
    c.close()
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
