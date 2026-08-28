"""Backfill language predictions for existing tracks via the VibeScape Modal API.

Iterates tracks in the app DB where `language IS NULL AND preview_url IS NOT NULL`,
calls the deployed `predict_language_from_url` Modal function, and UPDATEs the
row. Uses the same client wrapper (`ingest/ml_backend.py`) as the live ingest
hot path, so results are identical to what a fresh sync would produce.

Resume-safe: the SQL filter naturally skips rows that already have a language.
Re-runs after an interrupt only touch the still-NULL rows.

Requires MODAL_TOKEN_ID / MODAL_TOKEN_SECRET set (same env as prod ingest).

Usage:
    python ml/src/backfill_languages.py --limit 20            # smoke test
    python ml/src/backfill_languages.py                       # backfill everything
    python ml/src/backfill_languages.py --model tiny          # cheaper/faster
    python ml/src/backfill_languages.py --workers 4           # concurrent Modal calls
    python ml/src/backfill_languages.py --db path/to/app.db   # non-default DB path
    python ml/src/backfill_languages.py --confidence 0.5      # threshold for storing
"""

import argparse
import json
import signal
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "vibescape.db"

sys.path.insert(0, str(ROOT / "ingest"))

_stop = False


def _handle_sigint(_sig, _frame):
    global _stop
    print("\n[!] Ctrl+C received — finishing in-flight tracks. Ctrl+C again to force.", file=sys.stderr)
    _stop = True


signal.signal(signal.SIGINT, _handle_sigint)


def _resolve_db(path_arg: str | None) -> Path:
    if path_arg:
        return Path(path_arg).resolve()
    if DEFAULT_DB.exists():
        return DEFAULT_DB
    # Try a few likely fallbacks.
    for candidate in (ROOT / "vibescape.db", ROOT / "backend" / "vibescape.db"):
        if candidate.exists():
            return candidate
    return DEFAULT_DB  # will error clearly downstream


def load_pending(conn: sqlite3.Connection, limit: int | None,
                 retry_unknown: bool) -> list[tuple[int, str]]:
    """Return [(track_id, preview_url), ...] for rows still missing a language."""
    where = "preview_url IS NOT NULL AND preview_url <> ''"
    if retry_unknown:
        # Include rows where we tried before but got a low-confidence result
        # (stored as NULL language + non-null language_predicted_at, or truly-NULL).
        where += " AND language IS NULL"
    else:
        where += " AND language IS NULL AND language_predicted_at IS NULL"

    sql = f"SELECT id, preview_url FROM tracks WHERE {where} ORDER BY id"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [(int(r[0]), str(r[1])) for r in conn.execute(sql).fetchall()]


def update_track(conn: sqlite3.Connection, track_id: int, preds: dict | None,
                 confidence_threshold: float, model_size: str) -> str:
    """Write results back to DB. Returns status: confident/uncertain/unknown/failed."""
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")

    if not preds:
        # Modal call failed. Mark predicted_at so we don't spin on it every run,
        # unless caller passes --retry-unknown.
        conn.execute(
            "UPDATE tracks SET language_predicted_at = ?, "
            "language_model_version = ? WHERE id = ?",
            (now, f"whisper_{model_size}_failed", track_id),
        )
        return "failed"

    top1_prob = float(preds.get("top1_prob", 0.0))
    top3_json = json.dumps({
        "top1": [preds.get("top1_lang"), top1_prob],
        "top2": [preds.get("top2_lang"), float(preds.get("top2_prob", 0.0))],
        "top3": [preds.get("top3_lang"), float(preds.get("top3_prob", 0.0))],
    })
    model_version = str(preds.get("model_version") or f"whisper_{model_size}")

    if top1_prob >= confidence_threshold:
        status = "confident"
        lang = preds.get("top1_lang")
    elif top1_prob >= 0.2:
        status = "uncertain"
        lang = preds.get("top1_lang")
    else:
        status = "unknown"
        lang = None  # don't pollute the language column with low-confidence guesses

    conn.execute(
        "UPDATE tracks SET language = ?, language_confidence = ?, "
        "language_top3_json = ?, language_model_version = ?, "
        "language_predicted_at = ? WHERE id = ?",
        (lang, top1_prob if lang else None, top3_json, model_version, now, track_id),
    )
    return status


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Path to app SQLite DB (default: data/vibescape.db)")
    ap.add_argument("--limit", type=int, default=None, help="Only process first N rows (smoke test)")
    ap.add_argument("--model", default="small", choices=["tiny", "base", "small", "medium", "large-v3"],
                    help="Whisper model size sent to Modal (default: small)")
    ap.add_argument("--workers", type=int, default=4,
                    help="Concurrent Modal invocations (default 4). Modal handles the fan-out.")
    ap.add_argument("--confidence", type=float, default=0.5,
                    help="top1_prob threshold above which we store the language (default 0.5)")
    ap.add_argument("--retry-unknown", action="store_true",
                    help="Also re-attempt rows where a previous run gave up (low-conf/failed)")
    ap.add_argument("--progress-every", type=int, default=25, help="Print progress every N tracks")
    args = ap.parse_args()

    db_path = _resolve_db(args.db)
    if not db_path.exists():
        print(f"[x] DB not found: {db_path}", file=sys.stderr)
        print("    Pass --db /path/to/vibescape.db to override.", file=sys.stderr)
        sys.exit(1)

    try:
        import ml_backend
    except ImportError as e:
        print(f"[x] Cannot import ingest/ml_backend.py: {e}", file=sys.stderr)
        sys.exit(1)

    if not ml_backend.is_available():
        print("[x] No ML backend available.", file=sys.stderr)
        print("    Set VIBESCAPE_ML_MODE=local for in-process (GPU) inference,", file=sys.stderr)
        print("    or MODAL_TOKEN_ID / MODAL_TOKEN_SECRET for Modal.", file=sys.stderr)
        sys.exit(1)

    print(f"[*] DB: {db_path}")
    print(f"[*] ML backend mode: {ml_backend.current_mode()}")
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL")

    pending = load_pending(conn, args.limit, args.retry_unknown)
    print(f"[*] {len(pending):,} tracks pending language prediction")
    if not pending:
        print("[*] Nothing to do.")
        return

    counts = {"confident": 0, "uncertain": 0, "unknown": 0, "failed": 0}
    lang_counts: dict[str, int] = {}
    start = time.time()

    def worker(item: tuple[int, str]) -> tuple[int, dict | None]:
        tid, url = item
        preds = ml_backend.predict_language_from_url(url, args.model)
        return tid, preds

    # Concurrency: Modal handles the actual fan-out to warm containers.
    # We just need multiple in-flight requests so we're not I/O-bound serially.
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, p): p for p in pending}
        for i, fut in enumerate(as_completed(futures), 1):
            if _stop:
                pool.shutdown(wait=False, cancel_futures=True)
                break
            try:
                tid, preds = fut.result()
            except Exception as e:
                print(f"[!] worker error: {e}", file=sys.stderr)
                continue

            status = update_track(conn, tid, preds, args.confidence, args.model)
            conn.commit()
            counts[status] = counts.get(status, 0) + 1
            if preds and status in ("confident", "uncertain"):
                lg = preds.get("top1_lang") or "?"
                lang_counts[lg] = lang_counts.get(lg, 0) + 1

            if i % args.progress_every == 0 or i == len(pending):
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                remaining = len(pending) - i
                eta_min = (remaining / rate / 60) if rate > 0 else 0
                top_langs = sorted(lang_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
                top_str = " ".join(f"{lg}={n}" for lg, n in top_langs) or "-"
                print(
                    f"[{i:>6}/{len(pending)}] "
                    f"conf={counts['confident']:>5} uncert={counts['uncertain']:>4} "
                    f"unknown={counts['unknown']:>4} failed={counts['failed']:>3} "
                    f"| {rate:.2f} tr/s | ETA {eta_min:.1f} min | top: {top_str}",
                    flush=True,
                )

    elapsed = time.time() - start
    conn.close()
    print(f"\n[done] {elapsed/60:.1f} min  |  "
          f"confident={counts['confident']}  uncertain={counts['uncertain']}  "
          f"unknown={counts['unknown']}  failed={counts['failed']}")
    top_langs = sorted(lang_counts.items(), key=lambda kv: kv[1], reverse=True)[:15]
    if top_langs:
        print("       top languages stored:")
        for lg, n in top_langs:
            print(f"         {lg}: {n:,}")


if __name__ == "__main__":
    main()
