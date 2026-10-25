"""Spotify reads made on the caller's behalf, with the caller's Spotify
token from X-Spotify-Authorization: the library manifest the sync modal
shows and catalogue search. Plus /api/spotify/status, this process's view
of a Spotify 429 block, answered from memory."""
from typing import Optional

from bootstrap import log  # first: .env, sys.path (see bootstrap.py)

from fastapi import Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

# Through the module, never `from db import get_conn` (conftest.use_conn).
import db
from deps import _extract_bearer, require_user
from routers import Router
from track_rows import _isrc_of

import spotify_guard  # ingest/: every api.spotify.com call goes through it
import spotify_library as splib  # ingest/

router = Router()


@router.get("/api/spotify/status")
def spotify_status():
    """Is this server holding off Spotify after a 429? Answered from memory —
    checking never calls Spotify. {blocked, retry_after_s}. See
    ingest/spotify_guard.py. Per process: another Cloud Run instance learns
    of a block from its own first 429."""
    return spotify_guard.status()


@router.get("/api/spotify/library")
def spotify_library(
    spotify_authorization: Optional[str] = Header(None, alias="X-Spotify-Authorization"),
    authorization: Optional[str] = Header(None),
    sess: dict = Depends(require_user),
):
    # The VibeScape session token comes in Authorization: Bearer ...
    # The Spotify access token comes in X-Spotify-Authorization: Bearer ...
    # (Legacy: if X-Spotify-Authorization is missing, fall back to the
    # Authorization header — but that only works when VibeScape auth is
    # disabled, which is not the case in multi-user mode. We keep the
    # fallback for smoother migration; the frontend should send the
    # dedicated header.)
    token = _extract_bearer(spotify_authorization) or _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail={"error": "missing_spotify_authorization"})
    try:
        liked = splib.get_liked_count(token)
        top = splib.get_top_tracks_count(token)
        # No max_items: get_playlists owns the cap (500). Pinning it here is
        # how the earlier raise from 200 silently had no effect — the default
        # moved and the only caller kept overriding it.
        playlists_raw = splib.get_playlists(token)
        # Fetch the caller's Spotify user id so we can identify which
        # playlists are theirs (the only ones /items reliably returns 200
        # for after the Nov 2024 API lockdown). Fall back gracefully.
        my_spotify_id: Optional[str] = None
        try:
            me = splib._get(f"{splib.BASE}/me", token)
            my_spotify_id = me.get("id")
        except Exception as e:
            log.debug("could not resolve /v1/me for manifest owner-check: %s", e)
    except splib.SpotifyAuthError:
        return JSONResponse(status_code=401, content={"error": "spotify_token_expired"})
    except splib.SpotifyAPIError as e:
        raise HTTPException(status_code=500, detail=str(e))

    def _coerce_total(v) -> int:
        try:
            if v is None or v == "":
                return 0
            return int(v)
        except (TypeError, ValueError):
            return 0

    playlists = []
    to_resolve: list[str] = []  # playlists whose track_count came back empty/0
    for p in playlists_raw:
        if not p:
            continue
        owner_obj = p.get("owner") or {}
        owner_id = owner_obj.get("id") or ""
        owner_name = owner_obj.get("display_name") or owner_id or ""
        tracks_info = p.get("tracks") or {}
        total = _coerce_total(tracks_info.get("total"))
        pid = p.get("id")
        is_owned_by_caller = bool(my_spotify_id and owner_id == my_spotify_id)
        playlists.append({
            "id": pid,
            "name": p.get("name"),
            "track_count": total,
            "owner": owner_name,
            "owned_by_me": is_owned_by_caller,
        })
        # /me/playlists sometimes returns tracks.total as "" (empty string)
        # post Nov 2024 API changes. Only resolve via /items for playlists
        # the caller OWNS — third-party / editorial playlists return 403
        # on /items after the lockdown, so hitting them just burns rate
        # limit and spams warning logs for expected failures. If we can't
        # confirm ownership (my_spotify_id unknown), fall back to the
        # optimistic path and let the try/except quietly drop 403s.
        if total == 0 and pid and (is_owned_by_caller or my_spotify_id is None):
            to_resolve.append(pid)

    # Resolve real track counts via /items?limit=1&fields=total on
    # playlists we own. Failures are silent — UI falls back to 0.
    if to_resolve:
        for pid in to_resolve:
            try:
                data = splib._get(f"{splib.BASE}/playlists/{pid}/items", token,
                                  {"limit": 1, "fields": "total"})
                real_total = _coerce_total(data.get("total"))
                if real_total > 0:
                    for entry in playlists:
                        if entry["id"] == pid:
                            entry["track_count"] = real_total
                            break
            except splib.SpotifyAuthError:
                return JSONResponse(status_code=401,
                                    content={"error": "spotify_token_expired"})
            except splib.SpotifyAPIError as e:
                # 403 (deprecated / third-party lockdown) or other — expected
                # in post-Nov-2024 API. Log at debug to avoid warning-log spam.
                log.debug("manifest resolve for %s failed silently: %s", pid, e)
                continue

    return {
        "liked_count": liked,
        "top_tracks_count": top,
        "playlists": playlists,
    }


@router.get("/api/spotify/search")
def spotify_search(
    q: str = Query(..., min_length=1, max_length=200),
    # Spotify /v1/search caps `limit` at 10 as of late 2024 (docs say range 0-10,
    # default 5). Requesting 11+ returns 400 "Invalid limit".
    limit: int = Query(10, ge=1, le=10),
    spotify_authorization: Optional[str] = Header(None, alias="X-Spotify-Authorization"),
    sess: dict = Depends(require_user),
):
    """
    Search the Spotify catalog on behalf of the signed-in user. Returns a
    trimmed list of tracks with an `in_library` flag indicating whether
    the caller has already ingested that spotify_id, or another release of
    the same recording (same ISRC), which is what /api/ingest/single would
    link them to.
    """
    token = _extract_bearer(spotify_authorization)
    if not token:
        raise HTTPException(status_code=401, detail={"error": "missing_spotify_authorization"})
    query = (q or "").strip()
    if not query:
        return {"tracks": []}

    try:
        data = splib._get(
            f"{splib.BASE}/search",
            token,
            {"q": query, "type": "track", "limit": limit},
        )
    except splib.SpotifyAuthError:
        return JSONResponse(status_code=401, content={"error": "spotify_token_expired"})
    except splib.SpotifyAPIError as e:
        raise HTTPException(status_code=502, detail={"error": "spotify_api_error", "message": str(e)})

    items = ((data.get("tracks") or {}).get("items")) or []
    spotify_ids = [it.get("id") for it in items if isinstance(it, dict) and it.get("id")]
    isrcs = [_isrc_of(it) for it in items if isinstance(it, dict) and _isrc_of(it)]

    # For each Spotify id we recognise: pull vibe + mood so the frontend can
    # render "vibe NN" alongside the badge without a second round-trip.
    lib_meta: dict[str, dict] = {}
    lib_by_isrc: dict[str, dict] = {}
    if spotify_ids:
        placeholders = ",".join("?" * len(spotify_ids))
        isrc_ph = ",".join("?" * len(isrcs))
        conn = db.get_conn()
        try:
            rows = conn.execute(
                f"SELECT t.spotify_id, t.isrc, t.vibe_score, t.vibe_score_ml, "
                f"       t.mood, t.language, t.language_confidence "
                f"FROM tracks t "
                f"JOIN user_tracks ut ON ut.track_id = t.id "
                f"WHERE ut.user_id = ? AND (t.spotify_id IN ({placeholders})"
                f"{f' OR t.isrc IN ({isrc_ph})' if isrcs else ''})",
                [sess["user_id"], *spotify_ids, *isrcs],
            ).fetchall()
            for r in rows:
                meta = {
                    "vibe_score": r["vibe_score"],
                    "vibe_score_ml": r["vibe_score_ml"],
                    "mood": r["mood"],
                    "language": r["language"],
                    "language_confidence": r["language_confidence"],
                }
                if r["spotify_id"]:
                    lib_meta[r["spotify_id"]] = meta
                if r["isrc"]:
                    lib_by_isrc[r["isrc"]] = meta
        finally:
            conn.close()

    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("id"):
            continue
        sid = it.get("id")
        artists = it.get("artists") or []
        artist_name = ""
        if artists and isinstance(artists[0], dict):
            artist_name = artists[0].get("name") or ""
        album = it.get("album") or {}
        images = album.get("images") or []
        artwork_url = images[-1].get("url") if images and isinstance(images[-1], dict) else None
        meta = lib_meta.get(sid) or lib_by_isrc.get(_isrc_of(it)) or {}
        out.append({
            "spotify_id": sid,
            "title": it.get("name") or "",
            "artist": artist_name,
            "album": album.get("name") or "",
            "artwork_url": artwork_url,
            "preview_url": it.get("preview_url"),
            "duration_ms": it.get("duration_ms"),
            "in_library": bool(meta),
            "vibe_score": meta.get("vibe_score"),
            "vibe_score_ml": meta.get("vibe_score_ml"),
            "mood": meta.get("mood"),
            "language": meta.get("language"),
            "language_confidence": meta.get("language_confidence"),
        })
    return {"tracks": out}
