"""
Rebuild fused_embedding for every track that has a MERT vector, using the
current scalars + language columns. No audio download, no GPU — just the
fuse recipe (0.55·L2(MERT) ⊕ 0.25·L2(scalars_9) ⊕ 0.20·lang_onehot_11)
applied over data we already have.

Idempotent: rebuilding a fused vector for a track whose inputs haven't
changed produces the same bytes. Safe to re-run.

Runs against BOTH local sqlite and Turso in a single invocation. Match
happens per-DB via track_id (each DB uses its own autoincrement IDs;
we don't try to map across).

Run:
    D:/Softwares/MiniConda/python.exe scripts/_refuse_embeddings.py             # dry-run
    D:/Softwares/MiniConda/python.exe scripts/_refuse_embeddings.py --apply
    D:/Softwares/MiniConda/python.exe scripts/_refuse_embeddings.py --apply --local-only
    D:/Softwares/MiniConda/python.exe scripts/_refuse_embeddings.py --apply --turso-only
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
_LOCAL_DB = _REPO / "data" / "vibescape.db"
_PS1 = _REPO / "scripts" / "_load_gcp_secrets.ps1"


# ---- Feature spec — must stay in lockstep with ingest_pipeline/stage_embedding.py

MERT_DIM = 768
FUSED_DIM = 788

SCALAR_COLS = [
    "energy_pred", "danceability_pred", "valence_pred",
    "vibe_score", "activation", "valence",
    "acousticness", "tempo", "brightness",
]
SCALAR_RANGE = {
    "energy_pred":       (0.0, 1.0),
    "danceability_pred": (0.0, 1.0),
    "valence_pred":      (0.0, 1.0),
    "vibe_score":        (0.0, 100.0),
    "activation":        (0.0, 100.0),
    "valence":           (0.0, 100.0),
    "acousticness":      (0.0, 1.0),
    "tempo":             (40.0, 220.0),
    "brightness":        (0.0, 8000.0),
}
TOP_LANGS = ["en", "kn", "te", "hi", "pa", "sa", "ta", "ur", "km", "pt"]
LANG_DIMS = len(TOP_LANGS) + 1
W_MERT, W_SCALAR, W_LANG = 0.55, 0.25, 0.20


def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v if n < 1e-8 else v / n


def _norm_scalar(name: str, val) -> float:
    if val is None:
        return 0.0
    lo, hi = SCALAR_RANGE[name]
    if hi <= lo:
        return 0.0
    x = (float(val) - lo) / (hi - lo)
    return max(0.0, min(1.0, x))


def _lang_onehot(language: str | None) -> np.ndarray:
    v = np.zeros(LANG_DIMS, dtype=np.float32)
    if language:
        code = str(language).lower()
        v[TOP_LANGS.index(code) if code in TOP_LANGS else LANG_DIMS - 1] = 1.0
    else:
        v[-1] = 1.0
    return v


def _build_fused(scalars_dict: dict, language: str | None, mert_vec: np.ndarray) -> np.ndarray:
    scalars = np.array([_norm_scalar(c, scalars_dict.get(c)) for c in SCALAR_COLS],
                       dtype=np.float32)
    lang = _lang_onehot(language)
    fused = np.concatenate([
        W_MERT   * _l2(mert_vec.astype(np.float32, copy=False)),
        W_SCALAR * _l2(scalars),
        W_LANG   * lang,
    ])
    return _l2(fused)


def _load_turso_creds() -> tuple[str, str]:
    text = _PS1.read_text(encoding="utf-8")
    url = re.search(r'\$turso_url\s*=\s*"([^"]+)"', text).group(1)
    tok = re.search(r'\$turso_token\s*=\s*"([^"]+)"', text).group(1)
    return url, tok


def _fetch_rows_local(conn) -> list[dict]:
    """Local: MERT blob comes back as real bytes."""
    scalar_cols_sql = ", ".join("t." + c for c in SCALAR_COLS)
    rows = conn.execute(
        f"""
        SELECT t.id, t.language, {scalar_cols_sql},
               te.mert_embedding, te.fused_embedding
        FROM tracks t
        JOIN track_embeddings te ON te.track_id = t.id
        WHERE te.mert_embedding IS NOT NULL
        ORDER BY t.id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_rows_turso(conn) -> list[dict]:
    """Turso: F32_BLOB cells over HTTP need vector_extract() text form.
    We only need MERT for the fuse math — skip fused_embedding read
    (we're overwriting it)."""
    scalar_cols_sql = ", ".join("t." + c for c in SCALAR_COLS)
    rows = conn.execute(
        f"""
        SELECT t.id, t.language, {scalar_cols_sql},
               vector_extract(te.mert_embedding) AS mert_text
        FROM tracks t
        JOIN track_embeddings te ON te.track_id = t.id
        WHERE te.mert_embedding IS NOT NULL
        ORDER BY t.id
        """
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        mt = d.pop("mert_text", None) or ""
        s = mt.strip().lstrip("[").rstrip("]")
        if not s:
            continue
        vec = np.array([float(x) for x in s.split(",")], dtype=np.float32)
        d["mert_embedding"] = vec.tobytes()
        out.append(d)
    return out


def _refuse(rows: list[dict], label: str) -> list[tuple[int, bytes]]:
    """Return [(track_id, fused_bytes)] pairs to write."""
    updates: list[tuple[int, bytes]] = []
    for r in rows:
        mert_blob = r.get("mert_embedding")
        if not mert_blob:
            continue
        mert_vec = np.frombuffer(mert_blob, dtype=np.float32, count=MERT_DIM)
        if mert_vec.shape[0] != MERT_DIM:
            continue
        scalars = {c: r.get(c) for c in SCALAR_COLS}
        fused = _build_fused(scalars, r.get("language"), mert_vec)
        if fused.shape[0] != FUSED_DIM:
            continue
        updates.append((int(r["id"]), fused.astype(np.float32, copy=False).tobytes()))
    print(f"[{label}] recomputed {len(updates)} fused vectors")
    return updates


def _write_local(updates):
    conn = sqlite3.connect(str(_LOCAL_DB))
    for tid, blob in updates:
        conn.execute(
            "UPDATE track_embeddings SET fused_embedding = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE track_id = ?",
            (blob, tid),
        )
    conn.commit(); conn.close()
    print(f"  wrote {len(updates)} rows to local")


def _write_turso(conn, updates):
    n = 0
    for tid, blob in updates:
        try:
            conn.execute(
                "UPDATE track_embeddings SET fused_embedding = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE track_id = ?",
                (blob, tid),
            )
            n += 1
        except Exception as e:
            print(f"  turso update failed track_id={tid}: {e}")
        if n % 200 == 0 and n > 0:
            print(f"  ... {n} rows")
    print(f"  wrote {n} rows to Turso")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--local-only", action="store_true")
    ap.add_argument("--turso-only", action="store_true")
    args = ap.parse_args()

    # ---- local ----
    if not args.turso_only:
        lconn = sqlite3.connect(str(_LOCAL_DB)); lconn.row_factory = sqlite3.Row
        print("[local] fetching rows ...")
        rows = _fetch_rows_local(lconn); lconn.close()
        print(f"[local] fetched {len(rows)} rows")
        updates = _refuse(rows, "local")
        if args.apply:
            _write_local(updates)

    # ---- turso ----
    if not args.local_only:
        print()
        print("[turso] connecting ...")
        url, tok = _load_turso_creds()
        os.environ["TURSO_DATABASE_URL"] = url
        os.environ["TURSO_AUTH_TOKEN"]   = tok
        os.environ["DB_BACKEND"]         = "turso"
        sys.path.insert(0, str(_REPO / "backend"))
        sys.path.insert(0, str(_REPO / "ingest"))
        import db_client  # noqa: E402
        tconn = db_client.create_connection()
        print("[turso] fetching rows ...")
        rows = _fetch_rows_turso(tconn)
        print(f"[turso] fetched {len(rows)} rows")
        updates = _refuse(rows, "turso")
        if args.apply:
            _write_turso(tconn, updates)
        tconn.close()

    if not args.apply:
        print("\ndry-run — pass --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
