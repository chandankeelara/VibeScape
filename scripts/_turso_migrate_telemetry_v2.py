"""Telemetry v2 on Turso: widen track_events and user_track_stats, create
user_events.

Why this exists: backend/db.py's ensure_db() returns immediately on Turso, so
neither schema.sql nor _migrate_telemetry ever reaches production. Run this
BEFORE deploying the v2 backend. Until it has run, every POST /api/events on
the new code fails its INSERT ("no such column: listened_ms") and the events
are lost — the endpoint still answers 202 by design, so nothing will look
wrong from the client.

Idempotent: each column is added only if missing (the same column list as
the local migration, imported from backend/db.py so the two cannot differ),
and user_events is CREATE ... IF NOT EXISTS, read straight out of schema.sql.

Afterwards, populate the new counter for existing rows:
    python scripts/rebuild_user_track_stats.py --turso --apply

Credentials come from the environment, NOT from scripts/_load_gcp_secrets.ps1
(the repo rule is to never widen what reads that file):

    $env:DB_BACKEND="turso"; $env:TURSO_DATABASE_URL=...; $env:TURSO_AUTH_TOKEN=...
    python scripts/_turso_migrate_telemetry_v2.py            # report only
    python scripts/_turso_migrate_telemetry_v2.py --apply    # make the changes
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "backend"))


def _create_statements() -> list[str]:
    out = []
    for chunk in (_REPO / "schema.sql").read_text(encoding="utf-8").split(";"):
        body = "\n".join(ln for ln in chunk.splitlines()
                         if not ln.strip().startswith("--")).strip()
        head = body.upper().find("CREATE")
        if head >= 0 and ("EXISTS user_events" in body or "EXISTS idx_user_events" in body):
            out.append(body[head:])
    if len(out) != 2:
        raise SystemExit(f"expected user_events table + index in schema.sql, found {len(out)}")
    return out


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="make the changes (default: report)")
    args = ap.parse_args()

    if (os.environ.get("DB_BACKEND") or "").lower() not in ("turso", "libsql"):
        print("DB_BACKEND is not turso — refusing (this script is for production only).")
        return 1
    for k in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        if not os.environ.get(k):
            print(f"{k} is not set.")
            return 1

    import db_client  # noqa: E402
    from db import TRACK_EVENTS_ADDED_COLUMNS, USER_TRACK_STATS_ADDED_COLUMNS  # noqa: E402

    c = db_client.create_connection()
    todo = []
    for table, added in (("track_events", TRACK_EVENTS_ADDED_COLUMNS),
                         ("user_track_stats", USER_TRACK_STATS_ADDED_COLUMNS)):
        have = _columns(c, table)
        if not have:
            print(f"{table} does not exist — run the earlier telemetry scripts first.")
            return 1
        for col, decl in added:
            if col not in have:
                todo.append(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    todo += _create_statements()

    for stmt in todo:
        print(("  apply: " if args.apply else "  would: ") + " ".join(stmt.split())[:100])
        if args.apply:
            c.execute(stmt)
    if args.apply:
        c.commit()
        print("done. Next: python scripts/rebuild_user_track_stats.py --turso --apply")
    else:
        print("report only — rerun with --apply")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
