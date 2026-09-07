"""Standalone probe: run vector_distance_cos() against Turso to confirm
the fast-path SQL works before we ship the backend change."""
from __future__ import annotations

import os
import re
import struct
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
    import numpy as np  # noqa: E402

    c = db_client.create_connection()

    # 1) Get one existing fused_embedding to use as the query vector.
    # Turso may return F32_BLOB as a raw blob OR as a text/vector cell.
    # Ask for LENGTH to sanity-check on the server side, and try
    # vector_extract() to force text form.
    r = c.execute(
        "SELECT track_id, fused_embedding, LENGTH(fused_embedding) AS blen, "
        "       vector_extract(fused_embedding) AS as_text "
        "FROM track_embeddings WHERE fused_embedding IS NOT NULL LIMIT 1"
    ).fetchone()
    seed_id = int(r["track_id"])
    seed_blob = r["fused_embedding"]
    print(f"seed track_id={seed_id}  "
          f"server-side LENGTH={r['blen']}  "
          f"local type={type(seed_blob).__name__}  "
          f"local size={len(seed_blob) if hasattr(seed_blob,'__len__') else 'n/a'}")
    as_text = r["as_text"]
    if as_text:
        # Use vector_extract text form to reconstruct.
        print(f"vector_extract text head: {as_text[:60]}...")
        # Parse "[a, b, c, ...]" → floats
        parts = as_text.strip().lstrip("[").rstrip("]").split(",")
        seed_vec = np.array([float(x) for x in parts], dtype=np.float32)
    else:
        seed_vec = np.frombuffer(seed_blob, dtype=np.float32) if seed_blob else np.zeros(0, dtype=np.float32)
    print(f"seed vec dim={seed_vec.size}")

    # 2) Serialize to Turso vector32() format
    qv_str = "[" + ",".join(f"{float(x):.7f}" for x in seed_vec.tolist()) + "]"
    print(f"serialized query vec len={len(qv_str)} chars")

    # 3) Run vector_distance_cos top-K
    import time
    t0 = time.time()
    rows = c.execute(
        "SELECT track_id, "
        "       vector_distance_cos(fused_embedding, vector32(?)) AS distance "
        "FROM track_embeddings "
        "WHERE fused_embedding IS NOT NULL "
        "ORDER BY distance ASC "
        "LIMIT 5",
        (qv_str,),
    ).fetchall()
    elapsed = time.time() - t0
    print(f"\nquery finished in {elapsed*1000:.0f} ms — top 5:")
    for row in rows:
        print(f"  track_id={row['track_id']:5d}  distance={float(row['distance']):.6f}  "
              f"(cosine={1.0 - float(row['distance']):.6f})")

    # Sanity: the seed itself should have distance ~0
    if rows and int(rows[0]["track_id"]) == seed_id and float(rows[0]["distance"]) < 1e-4:
        print("\nOK — seed came back as the closest (distance ≈ 0).")
    else:
        print("\nWARN — seed wasn't the closest? something looks off.")

    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
