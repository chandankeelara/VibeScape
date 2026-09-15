"""Visualize MERT track embeddings in 3D.

Loads float32 BLOBs from `track_embeddings`, joins tracks for labels, reduces
the 768-dim vectors to 3D (PCA by default, t-SNE with --method tsne), and
writes an interactive plotly HTML you can open in any browser to spin/zoom/
hover the cloud.

Why PCA default: fast, deterministic, preserves global geometry — good
for a first look at whether MERT is producing meaningful clusters. t-SNE
is better at teasing apart local neighborhoods but takes longer and is
non-deterministic without a fixed seed.

Run:
    D:/Softwares/MiniConda/python.exe scripts/_visualize_embeddings_3d.py
    D:/Softwares/MiniConda/python.exe scripts/_visualize_embeddings_3d.py --method tsne --color language --limit 2000
    D:/Softwares/MiniConda/python.exe scripts/_visualize_embeddings_3d.py --color vibe_score_ml
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np


os.environ.setdefault("DB_BACKEND", "sqlite")
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))
import db_client  # noqa: E402


OUT_DIR = _REPO / "data"
OUT_DIR.mkdir(exist_ok=True)


COLOR_COLUMNS = {
    "mood": "categorical",
    "language": "categorical",
    "vibe_score": "numeric",
    "vibe_score_ml": "numeric",
    "energy_pred": "numeric",
    "danceability_pred": "numeric",
    "valence_pred": "numeric",
    "activation": "numeric",
    "valence": "numeric",
}


def load_embeddings(limit: int | None, model_version: str | None):
    """Return (X, meta_rows). X is (N, dim) float32; meta_rows is list of dicts."""
    conn = db_client.create_connection()
    where_bits = ["te.embedding IS NOT NULL"]
    args: list = []
    if model_version:
        where_bits.append("te.model_version = ?")
        args.append(model_version)
    where = " AND ".join(where_bits)
    sql = f"""
        SELECT t.id, t.title, t.artist, t.album, t.spotify_id, t.mood, t.language,
               t.vibe_score, t.vibe_score_ml, t.energy_pred, t.danceability_pred,
               t.valence_pred, t.activation, t.valence,
               te.dim, te.embedding, te.model_version
          FROM track_embeddings te
          JOIN tracks t ON t.id = te.track_id
         WHERE {where}
         ORDER BY t.id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, args).fetchall() if args else conn.execute(sql).fetchall()
    conn.close()
    if not rows:
        raise SystemExit("[x] No embeddings found in track_embeddings.")

    meta = []
    vecs = []
    dim = None
    for r in rows:
        d = int(r["dim"])
        if dim is None:
            dim = d
        elif d != dim:
            continue  # skip mixed dims for a clean matrix
        buf = r["embedding"]
        vec = np.frombuffer(buf, dtype=np.float32, count=d)
        if vec.shape[0] != d:
            continue
        vecs.append(vec)
        meta.append({k: r[k] for k in r.keys() if k not in ("embedding", "dim")})
    X = np.stack(vecs).astype(np.float32)
    print(f"[*] Loaded {X.shape[0]} embeddings, dim={X.shape[1]} (model_version={meta[0]['model_version']})")
    return X, meta


def reduce_to_3d(X: np.ndarray, method: str, seed: int) -> np.ndarray:
    # L2-normalize so cosine geometry maps to euclidean before projection.
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Xn = X / norms

    if method == "pca":
        from sklearn.decomposition import PCA
        p = PCA(n_components=3, random_state=seed)
        Y = p.fit_transform(Xn)
        var = p.explained_variance_ratio_
        print(f"[*] PCA explained variance: {var[0]:.3f} / {var[1]:.3f} / {var[2]:.3f}  (sum={var.sum():.3f})")
        return Y
    if method == "tsne":
        from sklearn.manifold import TSNE
        # Pre-reduce to 50D so t-SNE doesn't choke on 768D.
        from sklearn.decomposition import PCA
        pre = PCA(n_components=min(50, Xn.shape[1]), random_state=seed).fit_transform(Xn)
        perplexity = min(30, max(5, Xn.shape[0] // 50))
        print(f"[*] t-SNE: pre-PCA to {pre.shape[1]}D, perplexity={perplexity}")
        return TSNE(n_components=3, perplexity=perplexity, random_state=seed, init="pca").fit_transform(pre)
    raise SystemExit(f"[x] Unknown method: {method}")


def build_figure(Y: np.ndarray, meta: list, color: str, method: str):
    import plotly.express as px
    import pandas as pd

    df = pd.DataFrame({
        "x": Y[:, 0],
        "y": Y[:, 1],
        "z": Y[:, 2],
        "title": [m.get("title") or "" for m in meta],
        "artist": [m.get("artist") or "" for m in meta],
        "album": [m.get("album") or "" for m in meta],
        "spotify_id": [m.get("spotify_id") or "" for m in meta],
    })
    color_kind = COLOR_COLUMNS.get(color)
    if color_kind:
        df[color] = [m.get(color) for m in meta]
        # Cast None-heavy categoricals to strings so plotly assigns a legend entry.
        if color_kind == "categorical":
            df[color] = df[color].fillna("(none)").astype(str)

    hover = {"artist": True, "album": True, "spotify_id": True, "x": False, "y": False, "z": False}
    kwargs = dict(
        x="x", y="y", z="z",
        hover_name="title",
        hover_data=hover,
        title=f"Track embeddings — {method.upper()} 3D projection ({len(df)} tracks)",
    )
    if color_kind == "categorical":
        kwargs["color"] = color
        kwargs["color_discrete_sequence"] = px.colors.qualitative.Set3
    elif color_kind == "numeric":
        kwargs["color"] = color
        kwargs["color_continuous_scale"] = "Turbo"

    fig = px.scatter_3d(df, **kwargs)
    fig.update_traces(marker=dict(size=3.2, opacity=0.85, line=dict(width=0)))
    fig.update_layout(
        template="plotly_dark",
        margin=dict(l=0, r=0, t=48, b=0),
        scene=dict(
            xaxis=dict(showbackground=False, showticklabels=False, title=""),
            yaxis=dict(showbackground=False, showticklabels=False, title=""),
            zaxis=dict(showbackground=False, showticklabels=False, title=""),
        ),
        legend=dict(itemsizing="constant"),
    )
    return fig


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--method", default="pca", choices=["pca", "tsne"], help="Dim reduction (default: pca)")
    ap.add_argument("--color", default="mood", choices=list(COLOR_COLUMNS.keys()) + ["none"],
                    help="Which column to color points by (default: mood)")
    ap.add_argument("--limit", type=int, default=None, help="Cap number of tracks (default: all)")
    ap.add_argument("--model-version", default=None, help="Filter to a specific embedding model_version")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None, help="Output HTML path (default: data/embeddings_3d_<method>_<color>.html)")
    args = ap.parse_args()

    X, meta = load_embeddings(args.limit, args.model_version)
    Y = reduce_to_3d(X, args.method, args.seed)
    fig = build_figure(Y, meta, args.color if args.color != "none" else "mood", args.method)

    out = Path(args.out) if args.out else OUT_DIR / f"embeddings_3d_{args.method}_{args.color}.html"
    fig.write_html(str(out), include_plotlyjs="cdn", full_html=True)
    print(f"[OK] Wrote {out}")
    print(f"    Open in a browser to spin/zoom/hover.")


if __name__ == "__main__":
    main()
