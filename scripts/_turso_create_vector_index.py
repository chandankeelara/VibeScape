"""Create the DiskANN ANN index on Turso for fused_embedding.

Idempotent — the CREATE INDEX statement uses IF NOT EXISTS.

The index enables server-side top-K queries via
    SELECT id FROM vector_top_k('track_embeddings_fused_ann',
                                 vector32('[...]'), K);
which cuts per-request egress from ~4.7 MB (fetching every candidate's
embedding to numpy) down to ~10 KB (returning just K rowids + metadata).
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

    print("connecting to Turso ...")
    c = db_client.create_connection()
    print("creating ANN index on track_embeddings.fused_embedding ...")
    c.execute(
        "CREATE INDEX IF NOT EXISTS track_embeddings_fused_ann "
        "ON track_embeddings(libsql_vector_idx(fused_embedding))"
    )
    print("done.")
    # Verify by asking sqlite_master (libSQL keeps compatible schema table)
    rows = c.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'index' AND tbl_name = 'track_embeddings'"
    ).fetchall()
    print(f"indexes on track_embeddings ({len(rows)}):")
    for r in rows:
        sql = (r["sql"] or "").replace("\n", " ")[:120]
        print(f"  {r['name']}   {sql}")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
