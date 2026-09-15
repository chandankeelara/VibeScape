"""
Crop-length sensitivity test for the trained MERTVibeRegressor.

Question: if we unify the MERT encoder between ClassifyStage (currently 10 s
crop) and EmbeddingStage (30 s crop), the trained head would receive 30 s
features instead of 10 s. Does that meaningfully shift the vibe scores?

Method: for N random tracks with a preview URL, load the audio once, then
run the same MERTVibeRegressor twice — once with crop_duration_s=10 (the
current default), once with crop_duration_s=30. Compare the four output
targets per track and report MAE + worst cases.

If MAE on the 0-1 scaled outputs is ≪ 0.05, we can safely unify. If it's
≥ 0.10, the head needs retraining on 30 s crops before we unify.

Run:
    D:/Softwares/MiniConda/python.exe scripts/_predict_crop_length_test.py --n 30
"""
from __future__ import annotations

import argparse
import io
import os
import statistics
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

# Force UTF-8 stdout so unicode track titles don't crash cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT / "ml" / "src"))


def _download(url: str, suffix: str) -> Optional[str]:
    try:
        fd, p = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            with urllib.request.urlopen(url, timeout=30) as r:
                fh.write(r.read())
        return p
    except Exception as e:
        print(f"    download failed: {e}", flush=True)
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="sample size (default 30)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    # DB — grab N random tracks with preview_url + already scored
    import db_client  # noqa
    conn = db_client.create_connection()
    conn.row_factory = __import__("sqlite3").Row
    rows = conn.execute(
        """
        SELECT id, title, artist, preview_url
        FROM tracks
        WHERE preview_url IS NOT NULL AND preview_url != ''
          AND activation IS NOT NULL
        ORDER BY RANDOM() LIMIT ?
        """,
        (args.n,),
    ).fetchall()
    conn.close()
    if not rows:
        print("no eligible tracks; run classify first")
        return 1

    from predict import Predictor  # noqa: E402
    ckpt = str(PROJECT_ROOT / "ml" / "models" / "mert_v1.ckpt")
    print(f"loading MERTVibeRegressor from {ckpt} ...", flush=True)
    p10 = Predictor(ckpt, crop_duration_s=10.0)
    p30 = Predictor(ckpt, crop_duration_s=30.0)
    print("both predictors ready.\n", flush=True)

    # Targets returned by the head; we'll diff each.
    targets = list(p10.target_names) + ["vibe_score"]
    deltas: dict[str, list[float]] = {t: [] for t in targets}
    per_row: list[dict] = []

    for i, r in enumerate(rows, 1):
        url = r["preview_url"]
        suffix = ".m4a" if ".m4a" in url or "plus.aac" in url else ".mp3"
        tmp = _download(url, suffix)
        if tmp is None:
            print(f"[{i:2d}/{len(rows)}] {r['title']!r} — skip (download)")
            continue
        try:
            s10 = p10.predict(tmp)
            s30 = p30.predict(tmp)
        except Exception as e:
            print(f"[{i:2d}/{len(rows)}] {r['title']!r} — predict fail: {e}")
            continue
        finally:
            try: os.remove(tmp)
            except OSError: pass

        row_deltas = {}
        row_pair = {}  # target -> (s10, s30)
        for t in targets:
            row_deltas[t] = float(s30.get(t, 0.0)) - float(s10.get(t, 0.0))
            row_pair[t] = (float(s10.get(t, 0.0)), float(s30.get(t, 0.0)))
            deltas[t].append(row_deltas[t])

        # Report per row (compact)
        cols = "  ".join(f"{t}={s10[t]:+.3f}->{s30[t]:+.3f} d={row_deltas[t]:+.3f}"
                         for t in targets)
        print(f"[{i:2d}/{len(rows)}] {r['title'][:40]:40s} - {r['artist'][:20]:20s}")
        print(f"    {cols}")
        per_row.append({"id": r["id"], "title": r["title"], "artist": r["artist"],
                        "pair": row_pair, "delta": row_deltas})

    # Aggregate
    print("\n" + "=" * 78)
    print(f"Aggregate over {len(per_row)} tracks (delta = 30s - 10s, output range 0-1)")
    print("=" * 78)
    print(f"{'target':20s}  {'mean_delta':>10s}  {'std':>7s}  {'MAE':>7s}  {'max|d|':>7s}  {'p95|d|':>7s}")
    for t in targets:
        ds = deltas[t]
        if not ds:
            continue
        abs_ds = [abs(x) for x in ds]
        mean = statistics.mean(ds)
        std  = statistics.pstdev(ds) if len(ds) > 1 else 0.0
        mae  = statistics.mean(abs_ds)
        mx   = max(abs_ds)
        p95  = float(np.percentile(abs_ds, 95))
        print(f"  {t:18s}  {mean:+10.4f}  {std:7.4f}  {mae:7.4f}  {mx:7.4f}  {p95:7.4f}")

    # Top-5 biggest absolute deltas per target
    print("\n" + "=" * 78)
    print("Top 5 biggest |d| per target (10s -> 30s)")
    print("=" * 78)
    for t in targets:
        rows_sorted = sorted(per_row, key=lambda r: abs(r["delta"][t]), reverse=True)[:5]
        print(f"\n{t}:")
        for r in rows_sorted:
            s10v, s30v = r["pair"][t]
            d = r["delta"][t]
            print(f"  d={d:+.3f}  {s10v:+.3f} -> {s30v:+.3f}   "
                  f"{r['title'][:44]:44s} - {r['artist'][:22]:22s} (id={r['id']})")

    # Verdict thresholds
    print("\ninterpretation heuristic:")
    print("  MAE < 0.03  -> safe to unify without retraining")
    print("  MAE < 0.05  -> borderline; verify on a bigger sample")
    print("  MAE >= 0.05 -> retrain head on 30s crops before unifying")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
