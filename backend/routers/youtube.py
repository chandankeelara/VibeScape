"""YouTube video lookup for a track: the cached id, a manual yt-dlp search,
and pinning a video (admin only, it writes the global tracks.youtube_id).

_yt_manual_search_sync and _YT_SEARCH_TIMEOUT_S are patched by tests, so the
search route reads them as this module's globals at call time."""
import asyncio
import re
from typing import Optional

from bootstrap import log  # first: .env, sys.path (see bootstrap.py)

from fastapi import Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

# Through the module, never `from db import get_conn` (conftest.use_conn).
import db
from deps import require_admin, require_user
from routers import Router

router = Router()


_YT_SEARCH_TIMEOUT_S = 15.0


def _yt_search_sync(artist: str, title: str) -> Optional[str]:
    """
    Blocking yt-dlp search. Returns the first EMBEDDABLE 11-char YouTube
    video ID or None. Uses ytsearch5 across a query ladder, then validates
    each candidate via full extract (playable_in_embed True, age_limit 0,
    availability public/unlisted).
    """
    try:
        from yt_dlp import YoutubeDL
    except Exception:
        log.warning("[youtube] yt_dlp import failed")
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

    seen_ids: set = set()
    for q in queries:
        try:
            with YoutubeDL(flat_opts) as ydl:
                info = ydl.extract_info(q, download=False)
        except Exception as e:
            log.warning("[youtube] search failed for %r: %s", q, e)
            continue
        entries = info.get("entries") if isinstance(info, dict) else None
        if not entries:
            continue
        for entry in entries:
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


async def _yt_search_with_timeout(artist: str, title: str) -> Optional[str]:
    try:
        return await asyncio.wait_for(
            run_in_threadpool(_yt_search_sync, artist, title),
            timeout=_YT_SEARCH_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning("[youtube] search timed out for %r - %r", artist, title)
        return None
    except Exception as e:
        log.warning("[youtube] unexpected error for %r - %r: %s", artist, title, e)
        return None


@router.get("/api/tracks/{track_id}/youtube")
async def get_track_youtube(track_id: int, sess: dict = Depends(require_user)):
    """
    Return the cached YouTube video ID for a track, or null if we don't have
    one. Never triggers a search — resolution is done offline by
    scripts/prewarm_youtube.py so users never eat the ~3–5s yt-dlp latency.
    """
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT youtube_id FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail={"error": "track_not_found"})

    return {"youtube_id": row["youtube_id"], "cached": True}


_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _yt_manual_search_sync(query: str, limit: int) -> list[dict]:
    try:
        from yt_dlp import YoutubeDL
    except Exception:
        log.warning("[youtube] yt_dlp import failed")
        return []

    flat_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True,
        "extractor_args": {"youtube": {"player_client": ["default", "android", "web_embedded"]}},
    }

    try:
        with YoutubeDL(flat_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    except Exception as e:
        log.warning("[youtube] manual search failed for %r: %s", query, e)
        return []

    entries = info.get("entries") if isinstance(info, dict) else None
    if not entries:
        return []

    out: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        vid = entry.get("id")
        if not (isinstance(vid, str) and len(vid) == 11):
            continue
        title = entry.get("title") or entry.get("fulltitle")
        if not isinstance(title, str) or not title.strip():
            continue

        channel = entry.get("channel") or entry.get("uploader") or entry.get("channel_title")
        if not isinstance(channel, str):
            channel = None

        duration = entry.get("duration")
        if isinstance(duration, (int, float)):
            duration = int(duration)
        else:
            duration = None

        thumb = None
        thumbs = entry.get("thumbnails")
        if isinstance(thumbs, list) and thumbs:
            # Pick highest resolution by width*height, falling back to last.
            best = None
            best_area = -1
            for t in thumbs:
                if not isinstance(t, dict):
                    continue
                url = t.get("url")
                if not isinstance(url, str):
                    continue
                w = t.get("width") or 0
                h = t.get("height") or 0
                area = (w or 0) * (h or 0)
                if area > best_area:
                    best_area = area
                    best = url
            thumb = best or (thumbs[-1].get("url") if isinstance(thumbs[-1], dict) else None)
        if not thumb:
            thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

        out.append({
            "youtube_id": vid,
            "title": title,
            "channel": channel,
            "duration": duration,
            "thumbnail_url": thumb,
        })
        if len(out) >= limit:
            break
    return out


@router.get("/api/tracks/{track_id}/youtube/search")
async def search_track_youtube(
    track_id: int,
    q: str = Query(...),
    limit: int = Query(5),
    sess: dict = Depends(require_user),
):
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM tracks WHERE id = ?", (track_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "track_not_found"})

    query = (q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail={"error": "empty_query"})
    if len(query) > 300:
        raise HTTPException(status_code=400, detail={"error": "query_too_long"})

    n = max(1, min(int(limit), 10))

    try:
        results = await asyncio.wait_for(
            run_in_threadpool(_yt_manual_search_sync, query, n),
            timeout=_YT_SEARCH_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning("[youtube] manual search timed out for track=%s q=%r", track_id, query)
        results = []
    except Exception as e:
        log.warning("[youtube] manual search error for track=%s q=%r: %s", track_id, query, e)
        results = []

    return {"results": results}


@router.post("/api/tracks/{track_id}/youtube")
async def set_track_youtube(
    track_id: int,
    request: Request,
    sess: dict = Depends(require_admin),
):
    """
    Pin the YouTube video a track plays. Admin only: this writes the GLOBAL
    tracks.youtube_id, which every user (and the shared guest) streams from,
    so a per-user guard is not enough. Until 2026-10-13 it took require_user,
    meaning a guest minted by POST /api/auth/guest (no credentials) could
    repoint any track in the catalogue. Nothing in frontend-next calls this
    (lib/api.js:setTrackYoutube has no caller); it is a tooling endpoint.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "invalid_json"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "invalid_body"})
    vid = body.get("youtube_id")
    if not isinstance(vid, str) or not _YT_ID_RE.match(vid):
        raise HTTPException(status_code=400, detail={"error": "invalid_youtube_id"})

    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail={"error": "track_not_found"})
        conn.execute(
            "UPDATE tracks SET youtube_id = ?, youtube_queried_at = CURRENT_TIMESTAMP WHERE id = ?",
            (vid, track_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {"youtube_id": vid, "cached": True}
