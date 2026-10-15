"""
Dated snapshot of the PRODUCTION (Turso) database into a local SQLite file.

    python scripts/backup_prod_db.py

Writes data/backups/prod_YYYYMMDDTHHMMSS.db — gitignored, never committed.

WHY hex() AND NOT A PLAIN SELECT
--------------------------------
Until 2026-10-15 db_client's Hrana read path returned b"" for any BLOB whose
length is not a multiple of 3, every fused vector included: Hrana sends
base64 WITHOUT padding, plain base64.b64decode raised "Incorrect padding",
and a bare except swallowed it. A naive backup would have looked complete
while missing embeddings -- the worst possible failure for a backup, because
nothing announces it. db_client now decodes them and raises on a blob it
cannot.

BLOB columns are still read as hex(col), which the DATABASE evaluates, and
decoded here: the copy then does not depend on the client's decoder at all,
and the blob check at the end compares sizes the database reports.

Read-only against production. It opens one connection and only SELECTs.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _load_prod_env() -> None:
    """Credentials come from the same place every other ops script reads them.
    Never printed -- see the secrets rule in CLAUDE.md."""
    ps1 = _REPO / "scripts" / "_load_gcp_secrets.ps1"
    text = ps1.read_text(encoding="utf-8")
    os.environ["TURSO_DATABASE_URL"] = re.search(r'\$turso_url\s*=\s*"([^"]+)"', text).group(1)
    os.environ["TURSO_AUTH_TOKEN"] = re.search(r'\$turso_token\s*=\s*"([^"]+)"', text).group(1)
    os.environ["DB_BACKEND"] = "turso"


def main() -> int:
    _load_prod_env()
    import db_client  # imported after the env is set

    src = db_client.create_connection()

    out_dir = _REPO / "data" / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"prod_{stamp}.db"
    if out.exists():
        print(f"refusing to overwrite {out}")
        return 1

    dst = sqlite3.connect(str(out))
    tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]

    print(f"-> {out}")
    total_rows = 0
    for name in tables:
        info = src.execute(f"PRAGMA table_info({name})").fetchall()
        cols = [r[1] for r in info]
        types = [(r[2] or "").upper() for r in info]
        blobs = {c for c, t in zip(cols, types) if "BLOB" in t}

        ddl = src.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()[0]
        dst.execute(ddl)

        # hex() for blobs; everything else straight through. hex(NULL) is ''
        # in SQLite, so NULL is kept NULL explicitly — without the CASE, the
        # 28 tracks with no embedding yet came back as empty blobs and the
        # check below reported a mismatch (2026-10-12).
        select = ", ".join(
            f"CASE WHEN {c} IS NULL THEN NULL ELSE hex({c}) END" if c in blobs else c
            for c in cols)
        rows = src.execute(f"SELECT {select} FROM {name}").fetchall()

        out_rows = []
        for r in rows:
            vals = []
            for c, v in zip(cols, r):
                vals.append(bytes.fromhex(v) if (c in blobs and v is not None) else v)
            out_rows.append(tuple(vals))

        if out_rows:
            dst.executemany(
                f"INSERT INTO {name} ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})", out_rows)
        dst.commit()
        total_rows += len(out_rows)
        note = f"  (blobs via hex: {', '.join(sorted(blobs))})" if blobs else ""
        print(f"   {name:<22}{len(out_rows):>7} rows{note}")

    # Indexes, so the copy behaves like the original if it is ever restored.
    for (sql,) in src.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchall():
        try:
            dst.execute(sql)
        except sqlite3.OperationalError:
            pass
    dst.commit()

    # Prove the blobs actually survived rather than trusting the copy.
    try:
        prod_bytes = src.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(fused_embedding)),0) "
            "FROM track_embeddings WHERE fused_embedding IS NOT NULL").fetchone()
        local_bytes = dst.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(fused_embedding)),0) "
            "FROM track_embeddings WHERE fused_embedding IS NOT NULL").fetchone()
        print(f"\n   fused_embedding  prod {prod_bytes[0]} rows / {prod_bytes[1]} bytes")
        print(f"                    copy {local_bytes[0]} rows / {local_bytes[1]} bytes")
        print("   MATCH" if tuple(prod_bytes) == tuple(local_bytes) else "   *** MISMATCH ***")
    except Exception as e:
        print(f"   (blob check skipped: {e})")

    dst.close()
    print(f"\n{total_rows} rows, {out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
