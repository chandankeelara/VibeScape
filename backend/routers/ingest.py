"""Ingest routes: every /api/ingest/* endpoint. Clearing the caller's
library links; starting a Spotify library sync, a public-playlist import or
a playlist-link import (each creates its job dict in services/jobs.py's
JOBS and starts the worker from services/ingest_jobs.py); adding one track
synchronously (/api/ingest/single, through library_ingest._link_track);
and reading or cancelling a job.

Moved out of app.py unchanged (refactor PR 4, 2026-10-25). The three
initial job dicts are written out in their routes on purpose: their keys
sit in different positions, and that order is what /api/ingest/status
returns. Ingest here is only the online half: rows land pending and the
offline pipeline analyses them (backend/CLAUDE.md, the root CLAUDE.md).
"""
import threading
import uuid
from typing import Optional

from bootstrap import log  # first: .env, sys.path (see bootstrap.py)

import requests
from fastapi import Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

# Through the module, never `from db import get_conn` (conftest.use_conn).
import db
from deps import require_user
import playlist_import as _pli
from routers import Router
from track_rows import _row_to_dict, _track_select
from services.jobs import JOBS, JOBS_LOCK
from services.ingest_jobs import (IngestSources, _run_ingest_job, _run_playlist_url_job,
                                  _run_public_playlist_job)
# _get_app_token is patched by tests: read as spotify_auth._get_app_token.
from services import spotify_auth
# _process_track is patched by tests: read as library_ingest._process_track.
from services import library_ingest

import spotify_guard  # ingest/: every api.spotify.com call goes through it
import spotify_library as splib  # ingest/

router = Router()


# ---------------- Spotify library ingest ----------------


@router.post("/api/ingest/clear")
def ingest_clear(sess: dict = Depends(require_user)):
    """Remove the caller's library links (`user_tracks`) and nothing else.

    Catalogue rows are kept on purpose (2026-10-13). `tracks` is a global,
    song-level catalogue shared by every user, and on Turso, where foreign
    keys are enforced, `track_events`, `user_track_stats` and
    `track_embeddings` all reference `tracks(id) ON DELETE CASCADE`. This
    endpoint used to delete every track no other user still linked, plus its
    audio file, which permanently erased the caller's listening history
    (append-only, the source of truth) and the track's GPU-computed
    embeddings. Orphan catalogue rows, if they ever need cleaning, are an
    offline admin job, not a side effect of a user action.

    `tracks_pruned` and `audio_files_removed` are always 0 and kept only so
    existing clients that parse the body keep working.
    """
    conn = db.get_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM user_tracks WHERE user_id = ?", (sess["user_id"],)
        ).fetchone()[0]
        conn.execute("DELETE FROM user_tracks WHERE user_id = ?", (sess["user_id"],))
        conn.commit()
    finally:
        conn.close()

    log.info("ingest/clear user=%s removed %d user_tracks", sess["user_id"], n)
    return {"cleared": int(n), "tracks_pruned": 0, "audio_files_removed": 0}


class IngestRequest(BaseModel):
    access_token: str
    sources: IngestSources


class PublicPlaylistIngestRequest(BaseModel):
    # Either shape is accepted:
    #   {"playlist_url": "https://open.spotify.com/playlist/..."}
    #   {"playlist_id":  "37i9dQZF1DXcBWIGoYBM5M"}
    # Optional: the caller's Spotify OAuth access token. If provided we use
    # it for Spotify API calls (works for any playlist the user can see);
    # if omitted we fall back to the app's client-credentials token, which
    # since Nov 2024 is 403-forbidden for essentially all playlists.
    # The token is used only for the lifetime of this request/job — never
    # persisted server-side.
    playlist_url: Optional[str] = None
    playlist_id: Optional[str] = None
    access_token: Optional[str] = None


@router.post("/api/ingest/spotify", status_code=202)
def ingest_spotify(req: IngestRequest, sess: dict = Depends(require_user)):
    if not req.access_token:
        raise HTTPException(status_code=422, detail="access_token required")
    if not (req.sources.liked or req.sources.top_tracks or req.sources.playlist_ids):
        raise HTTPException(status_code=422, detail="no sources selected")
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "pending",
            "user_id": sess["user_id"],
            "current_track": None,
            "total": 0,
            "processed": 0,
            # True while Spotify pages are still arriving. `total` is a
            # running discovery count until this flips, so the UI should show
            # "found N so far" rather than a percentage of a moving target.
            "collecting": True,
            # Four mutually exclusive buckets (sum == processed) matching
            # the new two-phase ingest flow. See _process_track docstring.
            "added_to_library": 0,     # global row already done, just linked
            "already_in_library": 0,   # user already had this exact link
            "queued_for_analysis": 0,  # new/pending row, worker will finish it
            "skipped": 0,              # skip:no_id or exceptions
            "cancel_requested": False,
            "error_message": None,
        }
    t = threading.Thread(
        target=_run_ingest_job,
        args=(job_id, req.access_token, req.sources, sess["user_id"]),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id}


class SingleIngestRequest(BaseModel):
    spotify_id: str
    # Optional user OAuth token — required for /v1/tracks/{id} on premium/full
    # metadata. Falls back to app client-credentials token which works for
    # public track lookups.
    access_token: Optional[str] = None


@router.post("/api/ingest/single")
async def ingest_single(
    req: SingleIngestRequest,
    sess: dict = Depends(require_user),
):
    """
    Ingest a single Spotify track into the caller's library. Idempotent:
    if the caller already has this track, returns the existing row.
    Links through _link_track, the same path as playlist/liked ingest, so
    the track may come back as another release of the same recording (same
    ISRC) with a different spotify_id.
    """
    spotify_id = (req.spotify_id or "").strip()
    if not spotify_id:
        raise HTTPException(status_code=422, detail={"error": "missing_spotify_id"})

    user_id = sess["user_id"]

    conn = db.get_conn()
    try:
        row = conn.execute(
            f"SELECT {_track_select(conn)} FROM tracks t "
            f"JOIN user_tracks ut ON ut.track_id = t.id "
            f"WHERE t.spotify_id = ? AND ut.user_id = ?",
            (spotify_id, user_id),
        ).fetchone()
    finally:
        conn.close()
    if row:
        return {"status": "already_ingested", "track": _row_to_dict(row)}

    token = (req.access_token or "").strip()
    if not token:
        try:
            token = spotify_auth._get_app_token()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail={"error": "spotify_token_unavailable", "message": str(e)})

    try:
        track_obj = await run_in_threadpool(
            splib._get, f"{splib.BASE}/tracks/{spotify_id}", token
        )
    except splib.SpotifyAuthError:
        return JSONResponse(status_code=401, content={"error": "spotify_token_expired"})
    except splib.SpotifyAPIError as e:
        raise HTTPException(status_code=502, detail={"error": "spotify_api_error", "message": str(e)})

    if not isinstance(track_obj, dict) or not track_obj.get("id"):
        raise HTTPException(status_code=404, detail={"error": "track_not_found"})

    try:
        def _do_process() -> tuple[str, Optional[int]]:
            conn = db.get_conn()
            try:
                return library_ingest._link_track(conn, track_obj, user_id, source="search")
            finally:
                conn.close()
        result, track_id = await run_in_threadpool(_do_process)
    except Exception as e:
        log.exception("single ingest failed for %s: %s", spotify_id, e)
        raise HTTPException(status_code=500, detail={"error": "ingest_failed", "message": str(e)})

    if result.startswith("skip:no_id"):
        raise HTTPException(status_code=422, detail={"error": "invalid_track", "result": result})

    # By id: the row may be another release of the same recording, whose
    # spotify_id is not the one asked for (_link_track).
    conn = db.get_conn()
    try:
        row = conn.execute(
            f"SELECT {_track_select(conn)} FROM tracks t "
            f"JOIN user_tracks ut ON ut.track_id = t.id "
            f"WHERE t.id = ? AND ut.user_id = ?",
            (track_id, user_id),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=500, detail={"error": "post_ingest_missing", "result": result})
    return {"status": "ok", "result": result, "track": _row_to_dict(row)}


@router.post("/api/ingest/spotify-public", status_code=202)
def ingest_spotify_public(req: PublicPlaylistIngestRequest,
                         sess: dict = Depends(require_user)):
    """
    Ingest a public Spotify playlist by URL or ID. If the caller sends
    their Spotify OAuth `access_token` in the body we use it (works for
    arbitrary public playlists that the user can see, including friends'
    shared playlists). Otherwise falls back to the app's client-credentials
    token, which since Nov 2024 is 403-forbidden for essentially all
    playlists — kept for API completeness.

    New tracks are scoped to the caller's user_id. The Spotify OAuth token
    is used only for this request/job — never persisted.
    """
    raw = req.playlist_url or req.playlist_id or ""
    if not raw:
        raise HTTPException(
            status_code=422,
            detail={"error": "playlist_url or playlist_id required"},
        )
    playlist_id = splib.parse_playlist_id(raw)
    if not playlist_id:
        raise HTTPException(status_code=400, detail={"error": "invalid_playlist_url"})

    user_token = (req.access_token or "").strip() or None
    use_user_token = bool(user_token)
    followed_note: Optional[str] = None

    # Preflight: (1) confirm the playlist exists via metadata,
    #            (2) probe /tracks to see if we can actually read it
    #                (metadata succeeding does NOT imply tracks are readable
    #                 — confirmed live post Nov 2024),
    #            (3) if 403 with a user token, attempt silent follow +
    #                re-probe.
    try:
        preflight_token = user_token if use_user_token else spotify_auth._get_app_token()

        # (1) metadata
        r_meta = spotify_guard.get(
            f"https://api.spotify.com/v1/playlists/{playlist_id}",
            headers={"Authorization": f"Bearer {preflight_token}"},
            params={"fields": "id,name,tracks(total)"},
            timeout=15,
        )
        if r_meta.status_code == 404:
            raise HTTPException(status_code=404, detail={"error": "playlist_not_found"})
        if r_meta.status_code == 400:
            raise HTTPException(status_code=404, detail={"error": "playlist_not_found"})
        if r_meta.status_code == 401:
            if use_user_token:
                raise HTTPException(status_code=401,
                                    detail={"error": "spotify_token_expired"})
            raise HTTPException(status_code=403, detail={"error": "playlist_private"})
        if r_meta.status_code == 403:
            raise HTTPException(status_code=403, detail={"error": "playlist_private"})
        if r_meta.status_code >= 400:
            raise HTTPException(status_code=502,
                                detail={"error": f"spotify_api_error: {r_meta.status_code}"})

        # (2) probe tracks
        tracks_status = splib.probe_playlist_tracks(playlist_id, preflight_token)
        if tracks_status == 404:
            raise HTTPException(status_code=404, detail={"error": "playlist_not_found"})
        if tracks_status == 401:
            if use_user_token:
                raise HTTPException(status_code=401,
                                    detail={"error": "spotify_token_expired"})
            raise HTTPException(status_code=403, detail={"error": "playlist_private"})
        if tracks_status == 403:
            if not use_user_token:
                # App-token path: nothing we can do, Nov 2024 lockdown.
                raise HTTPException(status_code=403, detail={"error": "playlist_private"})
            # (3) user-token 403: try follow-then-retry.
            log.info("[public-playlist preflight] user=%s playlist=%s: /tracks 403, attempting follow",
                     sess["user_id"], playlist_id)
            try:
                follow_result = splib.follow_playlist(playlist_id, user_token, public=False)
            except splib.SpotifyAuthError:
                raise HTTPException(status_code=401,
                                    detail={"error": "spotify_token_expired"})
            except requests.RequestException as e:
                raise HTTPException(status_code=502,
                                    detail={"error": f"spotify_unreachable: {e}"})
            if follow_result == "scope":
                raise HTTPException(status_code=401,
                                    detail={"error": "spotify_scope_upgrade_required"})
            if follow_result != "ok":
                raise HTTPException(status_code=403,
                                    detail={"error": "playlist_private"})
            # Re-probe after follow.
            reprobe = splib.probe_playlist_tracks(playlist_id, user_token)
            if reprobe != 200:
                # Follow succeeded but tracks still not accessible — genuine.
                raise HTTPException(status_code=403,
                                    detail={"error": "playlist_private"})
            followed_note = ("Followed playlist to enable ingest — "
                             "visible in your Spotify library.")
            log.info("[public-playlist preflight] user=%s playlist=%s: follow+reprobe OK",
                     sess["user_id"], playlist_id)
        elif tracks_status >= 400:
            raise HTTPException(status_code=502,
                                detail={"error": f"spotify_api_error: {tracks_status}"})
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail={"error": f"spotify_unreachable: {e}"})

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "pending",
            "user_id": sess["user_id"],
            "current_track": None,
            "playlist_id": playlist_id,
            "source": "public_playlist",
            "auth_mode": "user_oauth" if use_user_token else "app_token",
            "note": followed_note,
            "total": 0,
            "processed": 0,
            # True while Spotify pages are still arriving. `total` is a
            # running discovery count until this flips, so the UI should show
            # "found N so far" rather than a percentage of a moving target.
            "collecting": True,
            # Four mutually exclusive buckets (sum == processed) matching
            # the new two-phase ingest flow. See _process_track docstring.
            "added_to_library": 0,     # global row already done, just linked
            "already_in_library": 0,   # user already had this exact link
            "queued_for_analysis": 0,  # new/pending row, worker will finish it
            "skipped": 0,              # skip:no_id or exceptions
            "cancel_requested": False,
            "error_message": None,
        }
    t = threading.Thread(
        target=_run_public_playlist_job,
        args=(job_id, playlist_id, sess["user_id"], user_token, followed_note),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id, "playlist_id": playlist_id, "note": followed_note}


# ---------------------------------------------------------------------------
# Import a public playlist from any supported service by link.
#
# Spotify, Apple Music, YouTube / YouTube Music and Amazon Music links are read by
# backend/playlist_import.py (its docstring says how and why for each), every
# song is matched to a Spotify track, and the match goes through the same
# _process_track as a library sync — so an imported song is a metadata row
# with ingestion_status='pending', or just a library link when the catalogue
# already has it. Songs with no confident match are reported, not guessed.
#
# Same job machinery as /api/ingest/spotify-public (thread + JOBS dict), so
# the same Cloud Run caveat applies (backlog 1.5); a ~100-song playlist takes
# about a minute, which the client's polling keeps alive.
# ---------------------------------------------------------------------------

class PlaylistUrlIngestRequest(BaseModel):
    url: str
    # The user's Spotify token, optional. Only used for Spotify links, where
    # it lifts the public embed's 100-song cap. Never persisted.
    access_token: Optional[str] = None


_PLATFORM_NAMES = {"spotify": "Spotify", "apple": "Apple Music", "youtube": "YouTube",
                   "deezer": "Deezer", "amazon": "Amazon Music"}


@router.post("/api/ingest/playlist-url", status_code=202)
def ingest_playlist_url(req: PlaylistUrlIngestRequest, sess: dict = Depends(require_user)):
    """Start importing a public playlist link. Returns {job_id, platform}.

    Errors (detail.error): invalid_playlist_url (400), and
    <service>_not_supported (400) for links we recognise but do not read yet
    (deezer). Everything that needs the network — not found,
    unreadable — is reported by the job, so this call stays fast.
    """
    src = _pli.detect(req.url or "")
    if not src:
        raise HTTPException(status_code=400, detail={"error": "invalid_playlist_url"})
    if src.kind not in _pli.SUPPORTED:
        raise HTTPException(status_code=400, detail={
            "error": f"{src.kind}_not_supported",
            "message": f"{_PLATFORM_NAMES.get(src.kind, src.kind)} playlists are not supported yet."})
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "pending",
            "user_id": sess["user_id"],
            "current_track": None,
            "source": f"playlist_url:{src.kind}",
            "platform": src.kind,
            "playlist_name": None,
            "note": None,
            "total": 0,
            "processed": 0,
            "collecting": True,
            "added_to_library": 0,
            "already_in_library": 0,
            "queued_for_analysis": 0,
            "skipped": 0,
            # Songs read from the source that matched nothing on Spotify.
            # A separate bucket from `skipped` (which is failures), with the
            # titles so the user can see exactly what did not come across.
            "not_matched": 0,
            "unmatched": [],
            "cancel_requested": False,
            "error_message": None,
        }
    user_token = (req.access_token or "").strip() or None
    threading.Thread(target=_run_playlist_url_job,
                     args=(job_id, src, sess["user_id"], user_token), daemon=True).start()
    return {"job_id": job_id, "platform": src.kind}


@router.get("/api/ingest/status/{job_id}")
def ingest_status(job_id: str, sess: dict = Depends(require_user)):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if int(job.get("user_id") or 0) != int(sess["user_id"]):
            raise HTTPException(status_code=404, detail="job not found")
        return dict(job)


@router.delete("/api/ingest/status/{job_id}", status_code=204)
def ingest_cancel(job_id: str, sess: dict = Depends(require_user)):
    """
    Request cancellation of an in-flight ingest job. Sets a flag the
    background runner polls between tracks; the runner exits cleanly on
    the next boundary and transitions the job to status='cancelled'.
    Tracks already inserted stay in the DB, audio files already saved
    stay on disk, and post-ingest z-score still runs so
    activation_relative reflects what did land.

    Idempotent: DELETE on an already-cancelled/completed/errored job
    returns 204 without side effects. Returns 404 if the job doesn't
    exist or belongs to another user (identical response to hide
    existence of other users' jobs).
    """
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if int(job.get("user_id") or 0) != int(sess["user_id"]):
            raise HTTPException(status_code=404, detail="job not found")
        # No-op on already-terminal jobs — still return 204 for idempotency.
        if job.get("status") in ("complete", "error", "cancelled"):
            return Response(status_code=204)
        job["cancel_requested"] = True
    log.info("[ingest] user=%s requested cancel for job=%s", sess["user_id"], job_id)
    return Response(status_code=204)
