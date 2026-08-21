"""Batch-run the trained MERT model over every track in the DB that has a
local audio file, and cache (energy_pred, danceability_pred, valence_pred,
vibe_score_ml, model_version) in the tracks table.

Resume-safe. Re-runs skip tracks that already have predictions for the same
model_version unless --force is passed.

Usage:
    python scripts/predict_ml.py                     # all pending tracks
    python scripts/predict_ml.py --limit 20          # smoke run
    python scripts/predict_ml.py --force             # re-predict everything
    python scripts/predict_ml.py --ckpt path.ckpt    # different checkpoint
"""

import argparse
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "vibescape.db"
DEFAULT_CKPT = ROOT / "ml" / "models" / "mert_v1.ckpt"

sys.path.insert(0, str(ROOT / "ml" / "src"))

_stop = threading.Event()


def _handle_sigint(_sig, _frame):
    print("\n[!] Ctrl+C — finishing current track, then exiting.", file=sys.stderr)
    _stop.set()


signal.signal(signal.SIGINT, _handle_sigint)


def fetch_todo(model_version: str, limit: int | None, force: bool) -> list[tuple[int, str]]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if force:
            sql = "SELECT id, audio_path FROM tracks WHERE audio_path IS NOT NULL ORDER BY id"
            params: tuple = ()
        else:
            sql = ("SELECT id, audio_path FROM tracks "
                   "WHERE audio_path IS NOT NULL "
                   "AND (energy_pred IS NULL OR model_version IS NULL OR model_version != ?) "
                   "ORDER BY id")
            params = (model_version,)
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [(r["id"], r["audio_path"]) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def write_result(track_id: int, preds: dict, model_version: str):
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        conn.execute(
            "UPDATE tracks SET energy_pred = ?, danceability_pred = ?, valence_pred = ?, "
            "vibe_score_ml = ?, model_version = ? WHERE id = ?",
            (preds["energy"], preds["danceability"], preds["valence"],
             preds["vibe_score"], model_version, track_id),
        )
        conn.commit()
    finally:
        conn.close()


def resolve_audio_path(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs)
    if p.is_absolute():
        return p
    return (ROOT / p).resolve()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", type=str, default=str(DEFAULT_CKPT))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="Re-predict tracks even if they already have predictions")
    ap.add_argument("--model-version", type=str, default=None,
                    help="Tag stored in model_version column (default: derived from checkpoint filename)")
    ap.add_argument("--progress-every", type=int, default=20)
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[x] DB not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    ckpt_path = Path(args.ckpt)
    if not ckpt_path.exists():
        print(f"[x] checkpoint not found: {ckpt_path}", file=sys.stderr)
        sys.exit(1)

    model_version = args.model_version or ckpt_path.stem
    print(f"[*] Loading model: {ckpt_path.name}  (tag: {model_version})")
    from predict import Predictor
    predictor = Predictor(str(ckpt_path))
    print(f"[*] Device: {predictor.device}  sample_rate: {predictor.sample_rate}")

    todo = fetch_todo(model_version, args.limit, args.force)
    print(f"[*] {len(todo):,} tracks pending prediction")
    if not todo:
        return

    counts = {"ok": 0, "missing_audio": 0, "error": 0}
    start = time.time()

    for i, (track_id, audio_path) in enumerate(todo, 1):
        if _stop.is_set():
            break
        p = resolve_audio_path(audio_path)
        if not p.exists():
            counts["missing_audio"] += 1
        else:
            try:
                preds = predictor.predict(str(p))
                write_result(track_id, preds, model_version)
                counts["ok"] += 1
            except Exception as e:
                counts["error"] += 1
                print(f"[!] track {track_id}: {type(e).__name__}: {e}", file=sys.stderr)

        if i % args.progress_every == 0 or i == len(todo):
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            remaining = len(todo) - i
            eta_min = (remaining / rate / 60) if rate > 0 else 0
            print(
                f"[{i:>4}/{len(todo)}] "
                f"ok={counts['ok']:>4} missing={counts['missing_audio']:>3} err={counts['error']:>2} "
                f"| {rate:.2f} tr/s | ETA {eta_min:.1f} min",
                flush=True,
            )

    elapsed = time.time() - start
    print(f"\n[done] {elapsed/60:.1f} min  |  "
          f"ok={counts['ok']}  missing_audio={counts['missing_audio']}  errors={counts['error']}")


if __name__ == "__main__":
    main()
