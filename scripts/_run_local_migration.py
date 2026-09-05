"""One-shot local helper: run db.py's ensure_db() against the LOCAL sqlite
file, then report the resulting tracks schema. Not for prod use."""
import os
import sys
from pathlib import Path

assert os.environ.get("DB_BACKEND", "sqlite").lower() == "sqlite", \
    "This script MUST run with DB_BACKEND=sqlite"
assert not os.environ.get("TURSO_DATABASE_URL"), \
    "TURSO_DATABASE_URL must be unset while running local migration"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from db import ensure_db, get_conn  # noqa: E402

print("[local-migrate] running ensure_db() on data/vibescape.db")
ensure_db()
print("[local-migrate] ensure_db() returned")

conn = get_conn()
try:
    tracks_cols = [r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()]
    ut_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_tracks'"
    ).fetchone() is not None
    print(f"tracks has user_id column: {'user_id' in tracks_cols}")
    print(f"user_tracks table exists: {ut_exists}")
    print(f"tracks row count: {conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0]}")
    if ut_exists:
        print(f"user_tracks row count: {conn.execute('SELECT COUNT(*) FROM user_tracks').fetchone()[0]}")
    print(f"tracks columns ({len(tracks_cols)}): {tracks_cols}")
finally:
    conn.close()
