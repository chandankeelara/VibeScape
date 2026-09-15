"""Quick read-only Turso verifier — counts rows in tracks + track_embeddings
and samples one embedding row to confirm the F32_BLOB columns landed."""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
_PS1 = _REPO / "scripts" / "_load_gcp_secrets.ps1"


def main() -> int:
    text = _PS1.read_text(encoding="utf-8")
    url = re.search(r'\$turso_url\s*=\s*"([^"]+)"', text).group(1)
    tok = re.search(r'\$turso_token\s*=\s*"([^"]+)"', text).group(1)
    os.environ["TURSO_DATABASE_URL"] = url
    os.environ["TURSO_AUTH_TOKEN"]   = tok
    os.environ["DB_BACKEND"]         = "turso"
    sys.path.insert(0, str(_REPO / "backend"))
    sys.path.insert(0, str(_REPO / "ingest"))
    import db_client  # noqa: E402

    c = db_client.create_connection()
    print("Turso track_embeddings CREATE:")
    try:
        r = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='track_embeddings'"
        ).fetchone()
        print(f"  {(r['sql'] if r else 'MISSING')}")
    except Exception as e:
        print(f"  err: {e}")
    print()
    print("Turso indexes on track_embeddings:")
    try:
        rows = c.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='index' AND tbl_name='track_embeddings'"
        ).fetchall()
        if not rows:
            print("  (none)")
        for r in rows:
            print(f"  {r['name']}   {(r['sql'] or '')[:120]}")
    except Exception as e:
        print(f"  err: {e}")
    print()
    print("Turso post-push state:")
    print(f"  tracks:           {c.execute('SELECT COUNT(*) FROM tracks').fetchone()[0]}")
    try:
        n = c.execute("SELECT COUNT(*) FROM track_embeddings").fetchone()[0]
        n_mert = c.execute("SELECT COUNT(*) FROM track_embeddings WHERE mert_embedding IS NOT NULL").fetchone()[0]
        n_fused = c.execute("SELECT COUNT(*) FROM track_embeddings WHERE fused_embedding IS NOT NULL").fetchone()[0]
        print(f"  track_embeddings: {n}")
        print(f"    with mert_embedding  NOT NULL: {n_mert}")
        print(f"    with fused_embedding NOT NULL: {n_fused}")
        # Sample one row to check blob sizes
        r = c.execute(
            "SELECT track_id, LENGTH(mert_embedding) mb, LENGTH(fused_embedding) fb "
            "FROM track_embeddings LIMIT 1"
        ).fetchone()
        if r:
            print(f"    sample row: track_id={r['track_id']}  mert_bytes={r['mb']}  fused_bytes={r['fb']}")
    except sqlite3.OperationalError as e:
        print(f"  track_embeddings: MISSING ({e})")
    try:
        print(f"  users:            {c.execute('SELECT COUNT(*) FROM users').fetchone()[0]}")
        print(f"  user_tracks:      {c.execute('SELECT COUNT(*) FROM user_tracks').fetchone()[0]}")
    except sqlite3.OperationalError:
        pass
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
