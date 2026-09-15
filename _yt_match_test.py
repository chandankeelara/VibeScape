"""Test: how many YT Music playlist tracks can we match on Spotify?

Usage:
    py _yt_match_test.py <playlist_url_or_id>
    py _yt_match_test.py <url> --limit 20      # smoke test
    py _yt_match_test.py <url> --dump-misses   # list every missed track

Auth: none needed for public playlists. Private/library playlists need
'headers.json' from browser cookies (see ytmusicapi docs).
"""
import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ingest"))

import config as app_config  # noqa: E402
import spotify_matcher  # noqa: E402


def parse_playlist_id(url_or_id: str) -> str:
    """Accepts full URL, music.youtube URL, or raw playlist id."""
    m = re.search(r"[?&]list=([A-Za-z0-9_-]+)", url_or_id)
    if m:
        return m.group(1)
    return url_or_id.strip()


def fetch_yt_playlist(playlist_id: str, headers_path: str | None):
    from ytmusicapi import YTMusic
    yt = YTMusic(headers_path) if headers_path else YTMusic()
    return yt.get_playlist(playlist_id, limit=None)


def artist_str(artists_field) -> str:
    """YTMusic track['artists'] is a list of {name, id}. Join first 2."""
    if not artists_field:
        return ""
    names = []
    for a in artists_field[:2]:
        if isinstance(a, dict) and a.get("name"):
            names.append(a["name"])
        elif isinstance(a, str):
            names.append(a)
    return " ".join(names)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("playlist", help="YT Music playlist URL or ID")
    ap.add_argument("--limit", type=int, default=None, help="Only try first N tracks")
    ap.add_argument("--headers", default=None, help="Path to ytmusicapi headers.json (private playlists)")
    ap.add_argument("--dump-misses", action="store_true", help="Print every unmatched track")
    ap.add_argument("--sleep", type=float, default=0.15, help="Sleep between Spotify calls (default 0.15s)")
    args = ap.parse_args()

    pid = parse_playlist_id(args.playlist)
    print(f"[*] Fetching YT Music playlist: {pid}")
    try:
        playlist = fetch_yt_playlist(pid, args.headers)
    except Exception as e:
        print(f"[x] failed to fetch playlist: {e}", file=sys.stderr)
        print("    If it's a private playlist, pass --headers <headers.json>.", file=sys.stderr)
        sys.exit(1)

    tracks = playlist.get("tracks", []) or []
    if args.limit:
        tracks = tracks[:args.limit]

    print(f"[*] Playlist: {playlist.get('title', '(untitled)')!r}")
    print(f"[*] Track count: {len(tracks)}")
    if not tracks:
        print("[x] no tracks in playlist")
        sys.exit(0)

    # Spotify client-credentials token
    cid = getattr(app_config, "SPOTIFY_CLIENT_ID", "")
    csec = getattr(app_config, "SPOTIFY_CLIENT_SECRET", "")
    if not cid or not csec:
        print("[x] Missing SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET (set env vars or config.py)")
        sys.exit(1)

    print("[*] Getting Spotify token")
    token = spotify_matcher.get_client_credentials_token(cid, csec)

    matched = 0
    misses = []
    start = time.time()

    for i, t in enumerate(tracks, 1):
        title = (t.get("title") or "").strip()
        artist = artist_str(t.get("artists"))
        if not title:
            misses.append(("(no title)", "", "n/a"))
            continue

        spotify_id = None
        try:
            spotify_id = spotify_matcher.match_by_title_artist(title, artist, token)
        except Exception as e:
            print(f"  [!] search error on {title!r}: {e}", file=sys.stderr)

        if spotify_id:
            matched += 1
            marker = f"OK spotify:{spotify_id}"
        else:
            misses.append((title, artist, t.get("videoId") or ""))
            marker = "MISS"

        # ascii-safe print
        line = f"  [{i:>3}/{len(tracks)}] {marker}  {title[:40]}  --  {artist[:35]}"
        print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)

        time.sleep(args.sleep)

    elapsed = time.time() - start
    pct = matched / len(tracks) * 100

    print()
    print(f"=== Match results ===")
    print(f"  playlist:  {playlist.get('title')!r}")
    print(f"  total:     {len(tracks)}")
    print(f"  matched:   {matched}  ({pct:.1f}%)")
    print(f"  missed:    {len(misses)}")
    print(f"  runtime:   {elapsed:.1f}s")

    if args.dump_misses and misses:
        print()
        print(f"=== All misses ({len(misses)}) ===")
        for title, artist, vid in misses:
            line = f"  MISS  {title[:45]:<45}  {artist[:30]:<30}  yt:{vid}"
            print(line.encode("ascii", errors="replace").decode("ascii"))
    elif misses:
        print()
        print(f"=== First 10 misses (of {len(misses)}) ===")
        for title, artist, vid in misses[:10]:
            line = f"  MISS  {title[:45]:<45}  {artist[:30]:<30}  yt:{vid}"
            print(line.encode("ascii", errors="replace").decode("ascii"))


if __name__ == "__main__":
    main()
