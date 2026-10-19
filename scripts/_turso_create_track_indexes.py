"""Create schema.sql's indexes on `tracks` where they are missing (backlog 1.13).

backend/db.py's ensure_db() returns at once on Turso, so schema.sql never
reached production, and on 2026-10-16 production's `tracks` had no index at
all: every stage gate and every _link_track lookup scanned the table, and
nothing stopped a duplicate spotify_id. This creates:

    idx_tracks_spotify_id        UNIQUE, WHERE spotify_id IS NOT NULL
    idx_tracks_apple_id          UNIQUE, WHERE apple_id IS NOT NULL
    idx_tracks_isrc              plain (every release of a recording shares it)
    idx_tracks_ingestion_status  plain

The statements are read out of schema.sql by name, so the two cannot drift.
The unique ones fail while duplicates remain: run scripts/_dedupe_tracks.py
first. This script checks for duplicates before trying and refuses with the
counts, rather than leaving the error to the CREATE.

    python scripts/_turso_create_track_indexes.py                       # local, report
    python scripts/_turso_create_track_indexes.py --apply               # local, create
    python scripts/_turso_create_track_indexes.py --target prod         # prod, report
    python scripts/_turso_create_track_indexes.py --target prod --apply

Idempotent: every statement is CREATE ... IF NOT EXISTS. Credentials for
--target prod come from .env.prod (scripts/_target.py), never from
scripts/_load_gcp_secrets.ps1.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO / "scripts"))

import _target  # noqa: E402

_SCHEMA = _REPO / "schema.sql"
INDEXES = ("idx_tracks_spotify_id", "idx_tracks_apple_id",
           "idx_tracks_isrc", "idx_tracks_ingestion_status")
_UNIQUE_ON = {"idx_tracks_spotify_id": "spotify_id", "idx_tracks_apple_id": "apple_id"}


def statements() -> dict:
    """{index name: its CREATE statement}, from schema.sql's lines."""
    out = {}
    for line in _SCHEMA.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(CREATE (?:UNIQUE )?INDEX IF NOT EXISTS (\w+)\s+ON tracks\s*\([^;]*)", line)
        if m and m.group(2) in INDEXES:
            out[m.group(2)] = " ".join(m.group(1).split())
    missing = [n for n in INDEXES if n not in out]
    if missing:
        raise SystemExit(f"schema.sql has no CREATE for {', '.join(missing)}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    _target.add_argument(ap)
    ap.add_argument("--apply", action="store_true", help="create them (default: report only)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    stmts = statements()
    print(_target.apply(args.target))
    import db_client
    conn = db_client.create_connection()

    have = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'tracks'")}
    todo = [n for n in INDEXES if n not in have]
    blocked = {}
    for name in todo:
        col = _UNIQUE_ON.get(name)
        if col:
            n = conn.execute(f"SELECT COUNT(*) FROM (SELECT {col} FROM tracks WHERE {col} IS NOT NULL "
                             f"GROUP BY {col} HAVING COUNT(*) > 1)").fetchone()[0]
            if n:
                blocked[name] = f"{n} duplicate {col} groups"
    for name in INDEXES:
        state = "exists" if name in have else (f"BLOCKED: {blocked[name]}" if name in blocked
                                               else "to create")
        print(f"  {name:28} {state}")
    if blocked:
        print("run scripts/_dedupe_tracks.py first; nothing was created.")
        return 1
    if not args.apply:
        print("dry-run: nothing was created. --apply to create.")
        return 0
    for name in todo:
        print(f"  {stmts[name]}")
        conn.execute(stmts[name])
    print(f"created {len(todo)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
