"""Predict spoken/sung language of downloaded Spotify previews using Whisper.

Iterates ml/data/previews/*.mp3, runs Whisper's language-detection head on the
first 30s of each file, and records the top-3 language guesses with their
probabilities into ml/data/language_manifest.csv.

Resume-safe: a manifest at ml/data/language_manifest.csv records status per
track_id. Re-runs skip tracks already marked confident/uncertain/unknown.
Pass --retry-failed to re-attempt tracks that previously failed.

Confidence tiers (based on top-1 probability):
    confident : >= --confidence-threshold  (default 0.5)
    uncertain : >= 0.2 and < threshold
    unknown   : < 0.2
    failed    : audio load / model error

Usage:
    python ml/src/predict_language.py --limit 20            # smoke test
    python ml/src/predict_language.py                       # process everything
    python ml/src/predict_language.py --model base          # smaller/faster
    python ml/src/predict_language.py --model small         # default, best size/quality
    python ml/src/predict_language.py --model medium        # larger, slower, better
    python ml/src/predict_language.py --retry-failed        # retry failed rows
    python ml/src/predict_language.py --compact-only        # dedupe manifest and exit

Requires: pip install openai-whisper (and ffmpeg on PATH).
"""

import argparse
import csv
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "ml" / "data"
PREVIEW_DIR = DATA / "previews"
MANIFEST = DATA / "language_manifest.csv"
LOG_FILE = DATA / "language_predict.log"

MANIFEST_FIELDS = [
    "track_id", "status",
    "top1_lang", "top1_prob",
    "top2_lang", "top2_prob",
    "top3_lang", "top3_prob",
    "attempted_at",
]

_stop = False


def _handle_sigint(_sig, _frame):
    global _stop
    print("\n[!] Ctrl+C received — finishing current track. Ctrl+C again to force.", file=sys.stderr)
    _stop = True


signal.signal(signal.SIGINT, _handle_sigint)


def log_event(kind: str, track_id: str, detail: str = ""):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{kind}\t{track_id}\t{detail}\n"
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line)


def ensure_manifest_header():
    if not MANIFEST.exists():
        with MANIFEST.open("w", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=MANIFEST_FIELDS).writeheader()


def load_manifest() -> dict:
    """Return {track_id: status} using the LATEST row per track_id."""
    if not MANIFEST.exists():
        return {}
    seen = {}
    with MANIFEST.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            seen[row["track_id"]] = row["status"]
    return seen


def load_manifest_full() -> dict:
    if not MANIFEST.exists():
        return {}
    rows = {}
    with MANIFEST.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows[row["track_id"]] = row
    return rows


def _count_manifest_lines() -> int:
    with MANIFEST.open("r", encoding="utf-8") as f:
        return sum(1 for _ in f) - 1


def compact_manifest() -> int:
    """Rewrite manifest as one row per track_id (latest wins)."""
    if not MANIFEST.exists():
        return 0
    rows = load_manifest_full()
    before = _count_manifest_lines()

    tmp = MANIFEST.with_suffix(".compact.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        for tid in sorted(rows.keys()):
            w.writerow(rows[tid])
    tmp.replace(MANIFEST)
    return before - len(rows)


def append_manifest(row: dict):
    with MANIFEST.open("a", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=MANIFEST_FIELDS).writerow(row)


def classify(status_top1_prob: float, threshold: float) -> str:
    if status_top1_prob >= threshold:
        return "confident"
    if status_top1_prob >= 0.2:
        return "uncertain"
    return "unknown"


def predict_one(model, whisper_mod, mp3_path: Path) -> tuple[list[tuple[str, float]], str | None]:
    """Return (top3, error). top3 is [(lang, prob), ...] sorted desc. On failure, top3=[] and error is set."""
    try:
        audio = whisper_mod.load_audio(str(mp3_path))
        audio = whisper_mod.pad_or_trim(audio)
        # Whisper 'large-v3' uses n_mels=128; older models use 80. Model exposes n_mels via config.
        n_mels = getattr(model, "dims", None)
        n_mels = n_mels.n_mels if n_mels is not None else 80
        mel = whisper_mod.log_mel_spectrogram(audio, n_mels=n_mels).to(model.device)
        _, probs = model.detect_language(mel)
        top3 = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)[:3]
        return top3, None
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None, help="Only process first N mp3 files (for testing)")
    ap.add_argument("--model", default="small", choices=["tiny", "base", "small", "medium", "large-v3"],
                    help="Whisper model size (default: small)")
    ap.add_argument("--device", default=None, help="cuda / cpu (default: auto)")
    ap.add_argument("--confidence-threshold", type=float, default=0.5,
                    help="top1 prob above which a prediction is 'confident' (default 0.5)")
    ap.add_argument("--retry-failed", action="store_true", help="Retry tracks previously marked failed")
    ap.add_argument("--retry-unknown", action="store_true", help="Also retry tracks marked unknown")
    ap.add_argument("--progress-every", type=int, default=50, help="Print progress every N tracks")
    ap.add_argument("--compact-only", action="store_true", help="Just compact the manifest and exit")
    args = ap.parse_args()

    if not PREVIEW_DIR.exists():
        print(f"[x] Missing previews dir: {PREVIEW_DIR}", file=sys.stderr)
        sys.exit(1)

    ensure_manifest_header()

    dropped = compact_manifest()
    if dropped > 0:
        print(f"[*] Compacted manifest (removed {dropped:,} duplicate rows)")

    if args.compact_only:
        print("[*] --compact-only flag set, exiting.")
        return

    try:
        import whisper  # openai-whisper
    except ImportError:
        print("[x] Missing dependency. Install with: pip install openai-whisper", file=sys.stderr)
        sys.exit(1)

    try:
        import torch
        default_device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        default_device = "cpu"
    device = args.device or default_device

    print(f"[*] Scanning {PREVIEW_DIR}")
    all_mp3s = sorted(PREVIEW_DIR.glob("*.mp3"))
    if args.limit is not None:
        all_mp3s = all_mp3s[:args.limit]
    print(f"[*] {len(all_mp3s):,} mp3 files found")

    manifest = load_manifest()
    print(f"[*] {len(manifest):,} previously attempted")

    skip = {"confident", "uncertain"}
    if not args.retry_unknown:
        skip.add("unknown")
    if not args.retry_failed:
        skip.add("failed")

    todo = [p for p in all_mp3s if manifest.get(p.stem) not in skip]
    print(f"[*] {len(todo):,} to process (skipping {sorted(skip)})")

    if not todo:
        print("[*] Nothing to do.")
        return

    print(f"[*] Loading Whisper model '{args.model}' on {device}")
    load_t0 = time.time()
    model = whisper.load_model(args.model, device=device)
    print(f"[*] Model loaded in {time.time() - load_t0:.1f}s")

    counts = {"confident": 0, "uncertain": 0, "unknown": 0, "failed": 0}
    lang_counts: dict[str, int] = {}
    start = time.time()

    for i, mp3 in enumerate(todo, 1):
        if _stop:
            break

        top3, err = predict_one(model, whisper, mp3)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")

        if err:
            log_event("predict_error", mp3.stem, err)
            row = {"track_id": mp3.stem, "status": "failed",
                   "top1_lang": "", "top1_prob": "",
                   "top2_lang": "", "top2_prob": "",
                   "top3_lang": "", "top3_prob": "",
                   "attempted_at": now}
        else:
            top3 = list(top3) + [("", 0.0)] * (3 - len(top3))
            status = classify(top3[0][1], args.confidence_threshold)
            row = {"track_id": mp3.stem, "status": status,
                   "top1_lang": top3[0][0], "top1_prob": f"{top3[0][1]:.4f}",
                   "top2_lang": top3[1][0], "top2_prob": f"{top3[1][1]:.4f}",
                   "top3_lang": top3[2][0], "top3_prob": f"{top3[2][1]:.4f}",
                   "attempted_at": now}
            if status in ("confident", "uncertain"):
                lang_counts[top3[0][0]] = lang_counts.get(top3[0][0], 0) + 1

        append_manifest(row)
        counts[row["status"]] = counts.get(row["status"], 0) + 1

        if i % args.progress_every == 0 or i == len(todo):
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            remaining = len(todo) - i
            eta_min = (remaining / rate / 60) if rate > 0 else 0
            top_langs = sorted(lang_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
            top_str = " ".join(f"{lg}={n}" for lg, n in top_langs) or "-"
            print(
                f"[{i:>6}/{len(todo)}] "
                f"conf={counts['confident']:>5}  uncert={counts['uncertain']:>4}  "
                f"unknown={counts['unknown']:>4}  failed={counts['failed']:>3}  "
                f"| {rate:.2f} tr/s | ETA {eta_min:.1f} min  | top: {top_str}",
                flush=True,
            )

    elapsed = time.time() - start
    dropped = compact_manifest()

    print(f"\n[done] {elapsed/60:.1f} min  |  "
          f"confident={counts['confident']}  uncertain={counts['uncertain']}  "
          f"unknown={counts['unknown']}  failed={counts['failed']}")
    top_langs = sorted(lang_counts.items(), key=lambda kv: kv[1], reverse=True)[:15]
    if top_langs:
        print("       top languages (confident + uncertain):")
        for lg, n in top_langs:
            print(f"         {lg}: {n:,}")
    if dropped > 0:
        print(f"       compacted manifest (removed {dropped:,} duplicate rows)")
    print(f"       manifest: {MANIFEST}")
    print(f"       event log: {LOG_FILE}")


if __name__ == "__main__":
    main()
