"""Resolve and cache the YouTube video ID for every track that hasn't been
queried yet.

Runs the same yt-dlp search ladder as backend/app.py::_yt_search_sync so the
prewarmed IDs match what the endpoint would have produced. Writes directly
to `data/vibescape.db`. Resume-safe: re-running skips anything already
queried (whether hit or miss).

Usage (from project root, or anywhere — paths are absolute-safe):

    python scripts/prewarm_youtube.py                 # all uncached tracks
    python scripts/prewarm_youtube.py --limit 100     # smoke run
    python scripts/prewarm_youtube.py --workers 4     # more concurrency
    python scripts/prewarm_youtube.py --sleep 0.25    # per-worker throttle
"""

import argparse
import signal
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "vibescape.db"

_stop = threading.Event()


def _handle_sigint(_sig, _frame):
    print("\n[!] Ctrl+C — finishing in-flight lookups. Ctrl+C again to force.", file=sys.stderr)
    _stop.set()


signal.signal(signal.SIGINT, _handle_sigint)


_COOKIE_BROWSER: str | None = None
_COOKIE_FILE: str | None = None


def yt_search(artist: str, title: str) -> str | None:
    """Return the first YouTube video ID that is actually embeddable, or None.

    Runs ytsearch5 across a ladder of progressively-relaxed queries, then
    validates each candidate via full extract until one passes the
    embeddability filter (playable_in_embed True, age_limit 0, availability
    public).
    """
    try:
        from yt_dlp import YoutubeDL
    except Exception as e:
        print(f"[!] yt_dlp import failed: {e}", file=sys.stderr)
        return None

    queries = [
        f'ytsearch5:"{title} - {artist} official music video"',
        f'ytsearch5:"{title} - {artist}"',
        f'ytsearch5:{title} {artist} official music video',
        f'ytsearch5:{title} {artist}',
        f'ytsearch5:{title}',
        f'ytsearch5:{artist}',
    ]
    flat_opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "noplaylist": True}
    full_opts = {
        "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
        "extractor_args": {"youtube": {"player_client": ["default", "android", "web_embedded"]}},
    }
    if _COOKIE_BROWSER:
        cookie_conf = (_COOKIE_BROWSER,)
        flat_opts["cookiesfrombrowser"] = cookie_conf
        full_opts["cookiesfrombrowser"] = cookie_conf
    if _COOKIE_FILE:
        flat_opts["cookiefile"] = _COOKIE_FILE
        full_opts["cookiefile"] = _COOKIE_FILE

    seen_ids: set[str] = set()
    for q in queries:
        if _stop.is_set():
            return None
        try:
            with YoutubeDL(flat_opts) as ydl:
                info = ydl.extract_info(q, download=False)
        except Exception:
            continue
        entries = info.get("entries") if isinstance(info, dict) else None
        if not entries:
            continue
        for entry in entries:
            if _stop.is_set():
                return None
            if not entry:
                continue
            vid = entry.get("id") if isinstance(entry, dict) else None
            if not (isinstance(vid, str) and len(vid) == 11):
                continue
            if vid in seen_ids:
                continue
            seen_ids.add(vid)
            try:
                with YoutubeDL(full_opts) as ydl:
                    full = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
            except Exception:
                continue
            if not full:
                continue
            if full.get("playable_in_embed") is False:
                continue
            if full.get("age_limit"):
                continue
            avail = full.get("availability")
            if avail not in (None, "public", "unlisted"):
                continue
            if full.get("live_status") in ("is_upcoming", "post_live"):
                continue
            return vid
    return None


def fetch_todo(limit: int | None) -> list[tuple[int, str, str]]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        sql = ("SELECT id, title, artist FROM tracks "
               "WHERE youtube_id IS NULL AND youtube_queried_at IS NULL "
               "ORDER BY id")
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [(r["id"], r["title"] or "", r["artist"] or "") for r in conn.execute(sql)]
    finally:
        conn.close()


def write_result(track_id: int, video_id: str | None):
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        conn.execute(
            "UPDATE tracks SET youtube_id = ?, youtube_queried_at = CURRENT_TIMESTAMP WHERE id = ?",
            (video_id, track_id),
        )
        conn.commit()
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None, help="Only process first N tracks (default: all)")
    ap.add_argument("--workers", type=int, default=3, help="Concurrent yt-dlp searches (default 3)")
    ap.add_argument("--sleep", type=float, default=0.0, help="Extra per-worker sleep between calls (default 0)")
    ap.add_argument("--progress-every", type=int, default=20, help="Print status every N tracks")
    ap.add_argument("--cookies-from-browser", type=str, default=None,
                    help="Browser to load YouTube cookies from (chrome, edge, firefox, brave, opera, vivaldi, chromium, safari). "
                         "Bypasses the anti-bot 'Sign in to confirm you're not a bot' block. "
                         "Browser must be fully closed while this runs (cookies DB is locked otherwise on Chromium browsers).")
    ap.add_argument("--cookies-file", type=str, default=None,
                    help="Path to a Netscape-format cookies.txt file exported from your browser. "
                         "Recommended on Windows since Chrome/Edge 127+ encrypt their cookie DB and "
                         "--cookies-from-browser will fail. Use a browser extension like "
                         "'Get cookies.txt LOCALLY' to export from YouTube.")
    args = ap.parse_args()

    global _COOKIE_BROWSER, _COOKIE_FILE
    _COOKIE_BROWSER = args.cookies_from_browser
    _COOKIE_FILE = args.cookies_file
    if _COOKIE_FILE and not Path(_COOKIE_FILE).exists():
        print(f"[x] cookies file not found: {_COOKIE_FILE}", file=sys.stderr)
        sys.exit(1)

    if not DB_PATH.exists():
        print(f"[x] DB not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    todo = fetch_todo(args.limit)
    print(f"[*] {len(todo):,} tracks pending YouTube resolution")
    if not todo:
        return

    counts = {"hit": 0, "miss": 0}
    counts_lock = threading.Lock()
    start = time.time()

    def worker(row):
        if _stop.is_set():
            return None
        tid, title, artist = row
        vid = yt_search(artist, title)
        write_result(tid, vid)
        with counts_lock:
            counts["hit" if vid else "miss"] += 1
        if args.sleep > 0:
            time.sleep(args.sleep)
        return (tid, vid)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker, row) for row in todo]
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
                hit_pct = counts["hit"] / i * 100 if i else 0
                print(
                    f"[{i:>4}/{len(todo)}] "
                    f"hits={counts['hit']:>4} ({hit_pct:.1f}%)  "
                    f"misses={counts['miss']:>3}  "
                    f"| {rate:.2f} tr/s  | ETA {eta_min:.1f} min",
                    flush=True,
                )

            if _stop.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                break

    elapsed = time.time() - start
    print(f"\n[done] {elapsed/60:.1f} min  |  "
          f"hits={counts['hit']}  misses={counts['miss']}  ({counts['hit']/(counts['hit']+counts['miss'])*100 if counts['hit']+counts['miss'] else 0:.1f}% hit rate)")


if __name__ == "__main__":
    main()
