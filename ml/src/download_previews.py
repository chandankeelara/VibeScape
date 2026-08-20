"""Download 30s Spotify previews for tracks in the Kaggle dataset.

Reads track IDs from ml/data/spotify_tracks.csv, scrapes each track's public
Spotify embed page to find the audioPreview URL, and downloads the MP3.

Resume-safe: a manifest at ml/data/manifest.csv records status per track.
Re-runs skip tracks already marked ok/no_preview. Pass --retry-failed to
re-attempt tracks that previously failed.

Usage:
    python ml/src/download_previews.py --limit 20        # smoke test
    python ml/src/download_previews.py                   # download everything
    python ml/src/download_previews.py --workers 5       # more concurrency
    python ml/src/download_previews.py --rps 2           # slower (be politer)
    python ml/src/download_previews.py --retry-failed    # retry download_failed rows
"""

import argparse
import csv
import re
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "ml" / "data"
CSV_IN = DATA / "spotify_tracks.csv"
PREVIEW_DIR = DATA / "previews"
MANIFEST = DATA / "manifest.csv"
LOG_FILE = DATA / "download.log"

EMBED_URL = "https://open.spotify.com/embed/track/{}"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
PREVIEW_RE = re.compile(r'"audioPreview":\{"url":"([^"]+)"')
MANIFEST_FIELDS = ["track_id", "status", "preview_url", "bytes", "attempted_at"]

_stop = threading.Event()
_manifest_lock = threading.Lock()
_counts_lock = threading.Lock()
_log_lock = threading.Lock()

_events = {"rate_limited": 0, "network_errors": 0, "retries": 0, "http_errors": 0}


def log_event(kind: str, track_id: str, detail: str = ""):
    """Append a timestamped event to download.log and bump counters."""
    with _counts_lock:
        _events[kind] = _events.get(kind, 0) + 1
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{kind}\t{track_id}\t{detail}\n"
    with _log_lock:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)


def _handle_sigint(_sig, _frame):
    print("\n[!] Ctrl+C received — finishing in-flight tasks. Ctrl+C again to force.", file=sys.stderr)
    _stop.set()


signal.signal(signal.SIGINT, _handle_sigint)


class RateLimit:
    """Simple global token-bucket rate limiter shared across worker threads."""

    def __init__(self, rps: float):
        self.interval = 1.0 / max(rps, 0.1)
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                time.sleep(self.next_at - now)
            self.next_at = max(now, self.next_at) + self.interval


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
    """Return {track_id: latest_full_row} for compaction."""
    if not MANIFEST.exists():
        return {}
    rows = {}
    with MANIFEST.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows[row["track_id"]] = row
    return rows


def compact_manifest():
    """Rewrite manifest as one row per track_id (latest attempt wins).

    Also cross-checks: if MP3 exists on disk but track missing from manifest,
    add it as ok. Prevents re-downloading orphaned files.
    """
    if not MANIFEST.exists():
        return 0
    rows = load_manifest_full()
    before = _count_manifest_lines()

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for mp3 in PREVIEW_DIR.glob("*.mp3"):
        tid = mp3.stem
        size = mp3.stat().st_size
        if size > 10_000 and (tid not in rows or rows[tid]["status"] != "ok"):
            rows[tid] = {"track_id": tid, "status": "ok",
                         "preview_url": rows.get(tid, {}).get("preview_url", ""),
                         "bytes": str(size), "attempted_at": now}

    tmp = MANIFEST.with_suffix(".compact.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        for tid in sorted(rows.keys()):
            w.writerow(rows[tid])
    tmp.replace(MANIFEST)
    return before - len(rows)


def _count_manifest_lines() -> int:
    with MANIFEST.open("r", encoding="utf-8") as f:
        return sum(1 for _ in f) - 1


def append_manifest(row: dict):
    with _manifest_lock:
        with MANIFEST.open("a", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=MANIFEST_FIELDS).writerow(row)


def load_track_ids(csv_path: Path, limit: int | None) -> list[str]:
    ids: list[str] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            if limit is not None and i >= limit:
                break
            ids.append(row["track_id"])
    seen: set[str] = set()
    unique: list[str] = []
    for t in ids:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def scrape_preview_url(session: requests.Session, track_id: str, limiter: RateLimit, timeout=15) -> str | None:
    limiter.wait()
    url = EMBED_URL.format(track_id)
    for attempt in range(3):
        if _stop.is_set():
            return None
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 5))
                log_event("rate_limited", track_id, f"scrape attempt={attempt} retry_after={wait}s")
                time.sleep(wait + attempt * 5)
                if attempt < 2:
                    log_event("retries", track_id, "scrape")
                continue
            if r.status_code == 404:
                log_event("http_errors", track_id, "scrape 404")
                return None
            r.raise_for_status()
            m = PREVIEW_RE.search(r.text)
            return m.group(1) if m else None
        except requests.RequestException as e:
            log_event("network_errors", track_id, f"scrape attempt={attempt} err={type(e).__name__}: {e}")
            if attempt < 2:
                log_event("retries", track_id, "scrape")
            time.sleep(2 ** attempt)
    return None


def download_mp3(session: requests.Session, url: str, path: Path, track_id: str, timeout=30) -> int:
    for attempt in range(3):
        if _stop.is_set():
            return 0
        try:
            with session.get(url, timeout=timeout, stream=True) as r:
                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", 5))
                    log_event("rate_limited", track_id, f"download attempt={attempt} retry_after={wait}s")
                    time.sleep(wait + attempt * 5)
                    if attempt < 2:
                        log_event("retries", track_id, "download")
                    continue
                if r.status_code >= 400:
                    log_event("http_errors", track_id, f"download HTTP {r.status_code}")
                r.raise_for_status()
                total = 0
                tmp = path.with_suffix(".part")
                with tmp.open("wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                        total += len(chunk)
                tmp.rename(path)
                return total
        except requests.RequestException as e:
            log_event("network_errors", track_id, f"download attempt={attempt} err={type(e).__name__}: {e}")
            if attempt < 2:
                log_event("retries", track_id, "download")
            time.sleep(2 ** attempt)
    return 0


def process_track(track_id: str, session: requests.Session, limiter: RateLimit) -> dict:
    out_path = PREVIEW_DIR / f"{track_id}.mp3"
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    if out_path.exists() and out_path.stat().st_size > 10_000:
        return {"track_id": track_id, "status": "ok",
                "preview_url": "", "bytes": out_path.stat().st_size, "attempted_at": now}

    preview_url = scrape_preview_url(session, track_id, limiter)
    if not preview_url:
        return {"track_id": track_id, "status": "no_preview",
                "preview_url": "", "bytes": 0, "attempted_at": now}

    n_bytes = download_mp3(session, preview_url, out_path, track_id)
    if n_bytes < 10_000:
        return {"track_id": track_id, "status": "download_failed",
                "preview_url": preview_url, "bytes": n_bytes, "attempted_at": now}

    return {"track_id": track_id, "status": "ok",
            "preview_url": preview_url, "bytes": n_bytes, "attempted_at": now}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None, help="Only process first N rows of CSV (for testing)")
    ap.add_argument("--workers", type=int, default=3, help="Concurrent worker threads (default 3)")
    ap.add_argument("--rps", type=float, default=3.0, help="Global scrape requests per second (default 3)")
    ap.add_argument("--retry-failed", action="store_true", help="Retry tracks previously marked download_failed")
    ap.add_argument("--progress-every", type=int, default=100, help="Print progress every N tracks")
    ap.add_argument("--compact-only", action="store_true", help="Just compact the manifest and exit (no downloading)")
    args = ap.parse_args()

    if not CSV_IN.exists():
        print(f"[x] Missing input CSV: {CSV_IN}", file=sys.stderr)
        sys.exit(1)

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    ensure_manifest_header()

    dropped = compact_manifest()
    if dropped > 0:
        print(f"[*] Compacted manifest (removed {dropped:,} duplicate rows)")

    if args.compact_only:
        print("[*] --compact-only flag set, exiting.")
        return

    print(f"[*] Loading track IDs from {CSV_IN.name}")
    all_ids = load_track_ids(CSV_IN, args.limit)
    print(f"[*] {len(all_ids):,} unique track IDs")

    manifest = load_manifest()
    print(f"[*] {len(manifest):,} previously attempted")

    skip = {"ok"} if args.retry_failed else {"ok", "no_preview"}
    todo = [t for t in all_ids if manifest.get(t) not in skip]
    print(f"[*] {len(todo):,} to process (skipping {sorted(skip)})")

    if not todo:
        print("[*] Nothing to do.")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    limiter = RateLimit(args.rps)

    counts = {"ok": 0, "no_preview": 0, "download_failed": 0}
    start = time.time()

    def worker(track_id: str):
        if _stop.is_set():
            return None
        row = process_track(track_id, session, limiter)
        append_manifest(row)
        with _counts_lock:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return row

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, t): t for t in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                fut.result()
            except Exception as e:
                print(f"[!] worker error: {e}", file=sys.stderr)

            if i % args.progress_every == 0 or i == len(todo):
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                remaining = len(todo) - i
                eta_min = (remaining / rate / 60) if rate > 0 else 0
                pct_ok = counts["ok"] / i * 100 if i else 0
                with _counts_lock:
                    ev = dict(_events)
                print(
                    f"[{i:>6}/{len(todo)}] "
                    f"ok={counts['ok']:>5} ({pct_ok:.1f}%)  "
                    f"no_preview={counts['no_preview']:>4}  "
                    f"failed={counts['download_failed']:>3}  "
                    f"| 429={ev['rate_limited']} neterr={ev['network_errors']} "
                    f"retry={ev['retries']} httperr={ev['http_errors']}  "
                    f"| {rate:.2f} tr/s  | ETA {eta_min:.1f} min",
                    flush=True,
                )

            if _stop.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                break

    elapsed = time.time() - start
    with _counts_lock:
        ev = dict(_events)

    dropped = compact_manifest()
    print(f"\n[done] {elapsed/60:.1f} min  |  "
          f"ok={counts['ok']}  no_preview={counts['no_preview']}  failed={counts['download_failed']}")
    print(f"       events: 429={ev['rate_limited']}  network_errors={ev['network_errors']}  "
          f"retries={ev['retries']}  http_errors={ev['http_errors']}")
    if dropped > 0:
        print(f"       compacted manifest (removed {dropped:,} duplicate rows)")
    print(f"       manifest: {MANIFEST}")
    print(f"       audio dir: {PREVIEW_DIR}")
    print(f"       event log: {LOG_FILE}")


if __name__ == "__main__":
    main()
