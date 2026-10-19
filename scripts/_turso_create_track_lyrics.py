"""Create track_lyrics (the lyrics cache, backend/lyrics.py) where it is missing.

backend/db.py's ensure_db() returns at once on Turso, so schema.sql never
reaches production. Until this has run there, GET /api/tracks/{key}/lyrics
still answers, but logs a warning and asks LRCLIB on every request.

    python scripts/_turso_create_track_lyrics.py                       # local, report
    python scripts/_turso_create_track_lyrics.py --apply               # local, create
    python scripts/_turso_create_track_lyrics.py --target prod         # prod, report
    python scripts/_turso_create_track_lyrics.py --target prod --apply

Idempotent (CREATE TABLE IF NOT EXISTS), the statement read out of
schema.sql so the two cannot drift. Credentials for --target prod come from
.env.prod (scripts/_target.py), never from scripts/_load_gcp_secrets.ps1.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO / "scripts"))

import _target  # noqa: E402

_SCHEMA = _REPO / "schema.sql"
TABLE = "track_lyrics"


def statement() -> str:
    """schema.sql's CREATE TABLE for track_lyrics, comments dropped."""
    for chunk in _SCHEMA.read_text(encoding="utf-8").split(";"):
        body = "\n".join(ln for ln in chunk.splitlines()
                         if not ln.strip().startswith("--")).strip()
        if body.upper().startswith("CREATE TABLE") and f"EXISTS {TABLE} " in body + " ":
            return body
    raise SystemExit(f"schema.sql has no CREATE TABLE for {TABLE}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    _target.add_argument(ap)
    ap.add_argument("--apply", action="store_true", help="create it (default: report only)")
    args = ap.parse_args(argv)

    stmt = statement()
    print(_target.apply(args.target))
    import db_client
    conn = db_client.create_connection()
    try:
        have = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
                            (TABLE,)).fetchone()[0]
        if have:
            print(f"  {TABLE} exists; nothing to do.")
            return 0
        print(f"  {TABLE} to create:\n{stmt}")
        if not args.apply:
            print("dry-run: nothing was created. --apply to create.")
            return 0
        conn.execute(stmt)
        conn.commit()
        print(f"created {TABLE}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
