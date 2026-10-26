"""The ingest job workers: the background threads behind POST
/api/ingest/spotify (_run_ingest_job: Liked Songs, top tracks and
playlists, streamed page by page), /api/ingest/spotify-public
(_run_public_playlist_job) and /api/ingest/playlist-url
(_run_playlist_url_job), with the helpers only they use.

Moved out of app.py unchanged (refactor PR 4, 2026-10-25). The routes that
start them create the job dict in services/jobs.py's JOBS before the thread
starts, so every worker writes a terminal status on every exit path
(backend/CLAUDE.md, "Background work"). Each worker opens its connection
with db.get_conn() at call time (conftest.use_conn), and reaches
_process_track as library_ingest._process_track, which tests patch.

Not services/library_ingest.py: that module is the synchronous row write,
also used by /api/ingest/single, with no thread, Spotify paging or job
state; these are the thread bodies and their Spotify and playlist reads.
"""
from typing import Iterator, Optional

import bootstrap  # noqa: F401  — first: puts ingest/ on sys.path
from bootstrap import log

import requests
from fastapi import HTTPException
from pydantic import BaseModel

# Through the module, never `from db import get_conn` (conftest.use_conn).
import db
import playlist_import as _pli
from services import library_ingest  # _process_track is patched by tests
from services import spotify_auth  # _get_app_token is patched by tests
from services.jobs import JOBS, JOBS_LOCK, _bump, _is_cancelled, _update_job

import spotify_guard  # noqa: E402  — every api.spotify.com call goes through it
import spotify_library as splib  # noqa: E402


class IngestSources(BaseModel):
    liked: bool = False
    top_tracks: bool = False
    playlist_ids: list[str] = []


def _iter_tracks(token: str, sources: IngestSources) -> Iterator[tuple[dict, str]]:
    """Yield (track, source) AS PAGES ARRIVE rather than after the whole fetch.

    Collecting everything first meant the job reported nothing at all until
    the last Spotify page landed — `total` is only known at the end, so the UI
    sat at 0/0 for the length of the fetch. On a large Liked Songs library
    that is 40 sequential round trips and ten-plus seconds of apparent
    deadlock, and it delayed the first playable track by exactly that long.

    Liked and top tracks stream, because those are the big ones. Playlists
    still arrive as a list: they are one or two pages each, and their fetcher
    carries dedup and diagnostic logging worth more than the second it saves.

    Dedup is global across sources, as before — a track in both Liked Songs
    and a playlist is yielded once.
    """
    seen: set[str] = set()

    def _fresh(t):
        tid = t.get("id") if isinstance(t, dict) else None
        if not tid or tid in seen:
            return False
        seen.add(tid)
        return True

    if sources.liked:
        for t in splib.iter_liked(token):
            if _fresh(t):
                yield t, "liked_songs"
    if sources.top_tracks:
        for t in splib.iter_top_tracks(token):
            if _fresh(t):
                yield t, "top_tracks"
    for pid in sources.playlist_ids or []:
        if not pid:
            continue
        for t in splib.fetch_playlist_tracks(pid, token):
            if _fresh(t):
                yield t, f"playlist:{pid}"


def _playable_first(conn, tracks, job_id):
    """Reorder a sync so the tracks that are INSTANTLY playable go first.

    A track already sitting in the catalogue at ingestion_status='done' costs
    one INSERT into user_tracks and is playable the moment it lands. A track
    we have never seen costs a metadata insert and then has to wait for the
    offline pipeline before it can be played at all.

    Processed in playlist order the two are interleaved, so a user importing
    500 tracks waits for the whole job before their library is usable — even
    though most of it was ready in the first second. Partitioning front-loads
    every instant win, which is what lets the UI offer "start listening"
    almost immediately instead of at 100%.

    This is pure ordering. No track is skipped, dropped or processed
    differently, and the four result buckets are unchanged — so a failure here
    must never break the sync. One query decides it; if that query fails for
    any reason we fall back to the original order and simply lose the
    optimisation.

    Stable within each half, so playlist order still shows through.
    """
    try:
        ids = [t.get("id") for t, _ in tracks if t.get("id")]
        if not ids:
            return tracks

        ready = set()
        # Chunked to stay under SQLite's variable limit on large libraries.
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            ph = ",".join("?" * len(chunk))
            ready.update(
                r[0] for r in conn.execute(
                    f"SELECT spotify_id FROM tracks "
                    f"WHERE ingestion_status = 'done' AND spotify_id IN ({ph})",
                    chunk,
                )
            )

        if not ready or len(ready) == len(ids):
            return tracks  # nothing to gain from reordering

        head = [p for p in tracks if p[0].get("id") in ready]
        tail = [p for p in tracks if p[0].get("id") not in ready]
        log.info(
            "[ingest job=%s] playable-first: %d instant, %d need analysis",
            job_id, len(head), len(tail),
        )
        return head + tail
    except Exception as e:
        log.warning("[ingest job=%s] playable-first reorder skipped: %s", job_id, e)
        return tracks


def _run_ingest_job(job_id: str, token: str, sources: IngestSources, user_id: int):
    try:
        _update_job(job_id, status="running", current_track="collecting library…")
        log.info("[ingest job=%s user=%s] starting collect: liked=%s top=%s playlists=%s "
                 "token_len=%d token_head=%s token_tail=%s",
                 job_id, user_id,
                 sources.liked, sources.top_tracks,
                 (sources.playlist_ids or []),
                 len(token or ""),
                 (token or "")[:12],
                 (token or "")[-6:] if token else "")

        # Preflight: probe /v1/me to distinguish "token is bad" from "endpoint
        # is bad". If /me returns 200, the token is fine and any downstream
        # 401 is endpoint-specific — bubble a clearer error.
        try:
            me = splib._get(f"{splib.BASE}/me", token)
            log.info("[ingest job=%s] /v1/me OK id=%s display=%s product=%s",
                     job_id, me.get("id"), me.get("display_name"), me.get("product"))
        except splib.SpotifyAuthError:
            log.warning("[ingest job=%s] /v1/me returned 401 — token itself is bad", job_id)
            _update_job(job_id, status="error", error_message="spotify_token_expired")
            return
        except Exception as e:
            log.warning("[ingest job=%s] /v1/me preflight failed: %s", job_id, e)

        conn = db.get_conn()
        by_src: dict[str, int] = {}
        try:
            """
            Fetch and process are INTERLEAVED.

            The old shape was collect-everything then process-everything, so
            `total` was unknown until the final Spotify page landed and the
            job reported 0/0 for the whole fetch. Now each page is processed
            as it arrives: the first tracks are written within about a second,
            which is what lets the player start and the sync window get out of
            the way almost immediately.

            `total` grows as pages arrive, so it is a running discovery count
            rather than a target until collection finishes. The job carries
            `collecting` so the UI can say "found 1,250 so far" instead of
            showing a percentage of a number that is still moving.
            """
            _update_job(job_id, collecting=True)
            batch: list[tuple[dict, str]] = []
            seen_total = 0
            cancelled = False

            def _drain(batch):
                """Process one page. Partitioned so instant wins land first."""
                for track, src in _playable_first(conn, batch, job_id):
                    if _is_cancelled(job_id):
                        return True
                    try:
                        result = library_ingest._process_track(conn, track, job_id, user_id, source=src)
                        if result in ("added_to_library", "already_in_library",
                                      "queued_for_analysis"):
                            _bump(job_id, result, 1)
                        else:
                            _bump(job_id, "skipped", 1)
                    except splib.SpotifyAuthError:
                        raise
                    except Exception as e:
                        log.exception("track ingest failed: %s", e)
                        _bump(job_id, "skipped", 1)
                    _bump(job_id, "processed", 1)
                return False

            try:
                for pair in _iter_tracks(token, sources):
                    by_src[pair[1]] = by_src.get(pair[1], 0) + 1
                    batch.append(pair)
                    seen_total += 1
                    # One Spotify page. Draining per page is what makes the
                    # first tracks playable while later pages are still in
                    # flight.
                    if len(batch) >= 50:
                        _update_job(job_id, total=seen_total)
                        if _drain(batch):
                            cancelled = True
                            break
                        batch = []
                if batch and not cancelled:
                    _update_job(job_id, total=seen_total)
                    if _drain(batch):
                        cancelled = True
            except splib.SpotifyAuthError:
                log.warning("[ingest job=%s] SpotifyAuthError mid-stream", job_id)
                _update_job(job_id, status="error", error_message="spotify_token_expired")
                return
            except splib.SpotifyAPIError as e:
                _update_job(job_id, status="error", error_message=f"spotify_api_error: {e}")
                return

            _update_job(job_id, total=seen_total, collecting=False)
            log.info("[ingest job=%s] streamed total=%d by_source=%s cancelled=%s",
                     job_id, seen_total, by_src, cancelled)
        finally:
            conn.close()

        if cancelled:
            _update_job(job_id, status="cancelled", current_track=None,
                        note="Cancelled by user")
        else:
            _update_job(job_id, status="complete", current_track=None)
    except Exception as e:
        log.exception("ingest job crashed: %s", e)
        _update_job(job_id, status="error", error_message=f"{e.__class__.__name__}: {e}")


def _run_public_playlist_job(job_id: str, playlist_id: str, user_id: int,
                             user_token: Optional[str] = None,
                             followed_note: Optional[str] = None):
    """
    Background job for /api/ingest/spotify-public. Uses the caller's Spotify
    OAuth token when supplied (works for arbitrary public playlists that
    the user follows or can follow), else falls back to the app's
    client-credentials token (largely broken since Nov 2024 but preserved
    for API completeness). Reuses the same _process_track pipeline as
    OAuth ingest — tracks land under `user_id`.

    If the tracks endpoint returns 403 with a user token, we attempt to
    silently follow the playlist ({"public": false}) — Spotify then
    unlocks the tracks endpoint. Follow policy: leave followed (matches
    user intent; frontend surfaces the note).

    The user's OAuth token is used in-memory only; nothing is persisted.
    """
    try:
        _update_job(job_id, status="running", current_track=f"loading playlist {playlist_id}…")
        if followed_note:
            _update_job(job_id, note=followed_note)

        use_user_token = bool(user_token)
        try:
            fetch_token = user_token if use_user_token else spotify_auth._get_app_token()
        except HTTPException as e:
            _update_job(job_id, status="error",
                        error_message=str(e.detail if hasattr(e, "detail") else e))
            return

        def _do_fetch(tok: str) -> list[dict]:
            return splib.fetch_public_playlist_tracks(playlist_id, tok)

        try:
            tracks = _do_fetch(fetch_token)
        except splib.PlaylistNotFoundError:
            _update_job(job_id, status="error", error_message="playlist_not_found")
            return
        except splib.PlaylistPrivateError:
            # 403 on /tracks. With a user token, try the follow-then-retry
            # workaround. With app token, this is genuinely inaccessible.
            if not use_user_token:
                _update_job(job_id, status="error", error_message="playlist_private")
                return
            log.info("[public-playlist] user=%s playlist=%s: /tracks 403, attempting follow-then-retry",
                     user_id, playlist_id)
            try:
                follow_result = splib.follow_playlist(playlist_id, user_token, public=False)
            except splib.SpotifyAuthError:
                _update_job(job_id, status="error",
                            error_message="spotify_token_expired")
                return
            except Exception as e:
                log.exception("[public-playlist] follow crashed: %s", e)
                _update_job(job_id, status="error", error_message="playlist_private")
                return
            if follow_result == "scope":
                _update_job(job_id, status="error",
                            error_message="spotify_scope_upgrade_required")
                return
            if follow_result != "ok":
                _update_job(job_id, status="error", error_message="playlist_private")
                return
            log.info("[public-playlist] user=%s playlist=%s: followed, retrying /tracks",
                     user_id, playlist_id)
            note = ("Followed playlist to enable ingest — "
                    "visible in your Spotify library.")
            _update_job(job_id, note=note)
            try:
                tracks = _do_fetch(user_token)
            except splib.PlaylistPrivateError:
                # Follow succeeded but tracks still 403 → genuinely restricted.
                _update_job(job_id, status="error", error_message="playlist_private")
                return
            except splib.PlaylistNotFoundError:
                _update_job(job_id, status="error", error_message="playlist_not_found")
                return
            except splib.SpotifyAuthError:
                _update_job(job_id, status="error",
                            error_message="spotify_token_expired")
                return
            except splib.SpotifyAPIError as e:
                _update_job(job_id, status="error",
                            error_message=f"spotify_api_error: {e}")
                return
        except splib.SpotifyAuthError:
            if use_user_token:
                _update_job(job_id, status="error", error_message="spotify_token_expired")
                return
            try:
                fetch_token = spotify_auth._get_app_token(force_refresh=True)
                tracks = _do_fetch(fetch_token)
            except Exception as e2:
                _update_job(job_id, status="error",
                            error_message=f"spotify_api_error: {e2}")
                return
        except splib.SpotifyAPIError as e:
            _update_job(job_id, status="error",
                        error_message=f"spotify_api_error: {e}")
            return

        # De-dupe within the fetch (Spotify sometimes returns dupes in playlists).
        seen: set[str] = set()
        unique_tracks: list[dict] = []
        for t in tracks:
            tid = t.get("id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            unique_tracks.append(t)

        # collecting=False matters as much as the total here.
        #
        # This path fetches the whole playlist up front, so the count is exact
        # the moment it lands — there is no discovery phase to report. The job
        # dict still starts at collecting=True to match the streaming library
        # job's shape, and nothing here ever cleared it: the progress bar
        # stayed indeterminate for the entire import and remained so after the
        # status went 'complete'.
        _update_job(job_id, total=len(unique_tracks), collecting=False)

        conn = db.get_conn()
        try:
            cancelled = False
            src_label = f"playlist:{playlist_id}"
            for track in unique_tracks:
                if _is_cancelled(job_id):
                    log.info("[public-playlist job=%s] cancel_requested — stopping loop", job_id)
                    cancelled = True
                    break
                try:
                    result = library_ingest._process_track(conn, track, job_id, user_id, source=src_label)
                    # Same 4-bucket scheme as _run_ingest_job.
                    if result == "added_to_library":
                        _bump(job_id, "added_to_library", 1)
                    elif result == "already_in_library":
                        _bump(job_id, "already_in_library", 1)
                    elif result == "queued_for_analysis":
                        _bump(job_id, "queued_for_analysis", 1)
                    else:
                        _bump(job_id, "skipped", 1)
                except Exception as e:
                    log.exception("public playlist track ingest failed: %s", e)
                    _bump(job_id, "skipped", 1)
                _bump(job_id, "processed", 1)
        finally:
            conn.close()

        if cancelled:
            # Preserve any existing followed_note; add a cancelled note only
            # if there isn't already a meaningful one.
            with JOBS_LOCK:
                existing_note = (JOBS.get(job_id) or {}).get("note")
            new_note = existing_note or "Cancelled by user"
            _update_job(job_id, status="cancelled", current_track=None, note=new_note)
        else:
            _update_job(job_id, status="complete", current_track=None)
    except Exception as e:
        log.exception("public playlist job crashed: %s", e)
        _update_job(job_id, status="error", error_message=f"{e.__class__.__name__}: {e}")


_UNMATCHED_REPORT_CAP = 200


def _catalog_match(conn, item):
    """A song the catalogue already has, as a Spotify-shaped track — so it
    costs no Spotify call. By ISRC when the source gave one, else by exact
    (case-insensitive) title, scored with the same matcher as search."""
    cols = "spotify_id, title, artist, album, artwork_url, duration_ms, isrc"
    rows = []
    if item.isrc:
        rows = conn.execute(f"SELECT {cols} FROM tracks WHERE isrc = ? AND spotify_id IS NOT NULL LIMIT 1",
                            (item.isrc,)).fetchall()
    if not rows and item.title:
        rows = conn.execute(f"SELECT {cols} FROM tracks WHERE LOWER(title) = LOWER(?) "
                            f"AND spotify_id IS NOT NULL LIMIT 10", (item.title,)).fetchall()
    cands = [{"id": r["spotify_id"], "name": r["title"], "artists": [{"name": r["artist"]}],
              "album": {"name": r["album"], "images": [{"url": r["artwork_url"]}] if r["artwork_url"] else []},
              "duration_ms": r["duration_ms"], "external_ids": {"isrc": r["isrc"]} if r["isrc"] else {},
              "preview_url": None} for r in rows]
    best, _score = _pli.choose(item, cands)
    return best


def _run_playlist_url_job(job_id: str, src, user_id: int, user_token: Optional[str]):
    try:
        _update_job(job_id, status="running", current_track=f"reading {src.kind} playlist…")

        # Spotify + the user's own token: the Web API returns the whole
        # playlist with full track objects. Anything else, or any failure
        # there, falls back to the public readers.
        direct = None
        if src.kind == "spotify" and user_token:
            try:
                direct = splib.fetch_public_playlist_tracks(src.id, user_token)
            except Exception as e:  # 403/404/expired: the public embed still works
                log.info("[playlist-url job=%s] Spotify API read failed (%s); using public embed",
                         job_id, e.__class__.__name__)
        if direct is not None:
            work = [(t, None) for t in direct if t and t.get("id")]
        else:
            try:
                pl = _pli.fetch(src, requests.get)
            except _pli.ImportError_ as e:
                _update_job(job_id, status="error", error_message=e.code)
                return
            _update_job(job_id, playlist_name=pl.name, note=pl.note)
            work = [(None, it) for it in pl.items]
        _update_job(job_id, total=len(work), collecting=False)

        conn = db.get_conn()
        cancelled = False
        rate_limited = None
        seen: set = set()
        src_label = f"{src.kind}:{src.id}"[:120]
        try:
            for track, item in work:
                if _is_cancelled(job_id):
                    cancelled = True
                    break
                counted = True               # every outcome counts as processed but a rate-limit stop
                try:
                    if track is None:
                        _update_job(job_id, current_track=f"matching {item.label()}")
                        track, why = _pli.resolve(item, spotify_auth._get_app_token(), spotify_guard.get,
                                                  catalog=lambda it: _catalog_match(conn, it))
                        if track is None:
                            _bump(job_id, "not_matched", 1)
                            with JOBS_LOCK:
                                lst = JOBS[job_id]["unmatched"]
                                if len(lst) < _UNMATCHED_REPORT_CAP:
                                    lst.append({"title": item.title, "artist": item.artist})
                            continue
                    sid = track.get("id")
                    if sid in seen:              # the same song twice in one playlist
                        _bump(job_id, "already_in_library", 1)
                        continue
                    seen.add(sid)
                    result = library_ingest._process_track(conn, track, job_id, user_id, source=src_label)
                    bucket = result if result in ("added_to_library", "already_in_library",
                                                  "queued_for_analysis") else "skipped"
                    _bump(job_id, bucket, 1)
                except _pli.SpotifyRateLimited as e:
                    # Stop, don't retry: retrying into a long Retry-After is
                    # how the app earned a 19.5 h block on 2026-10-10. What
                    # already landed stays; the rest is never marked
                    # "not matched", because it was never tried.
                    rate_limited, counted = e, False
                    break
                except Exception as e:
                    log.exception("[playlist-url job=%s] track failed: %s", job_id, e)
                    _bump(job_id, "skipped", 1)
                finally:
                    if counted:
                        _bump(job_id, "processed", 1)
        finally:
            conn.close()

        if rate_limited is not None:
            hours = rate_limited.retry_after_s / 3600
            _update_job(job_id, status="error", current_track=None,
                        error_message="spotify_rate_limited",
                        note=(f"Spotify is limiting VibeScape right now (try again in about "
                              f"{hours:.0f} h). Songs added so far are kept."
                              if hours >= 1 else
                              "Spotify is limiting VibeScape right now — try again in a few "
                              "minutes. Songs added so far are kept."))
            log.warning("[playlist-url job=%s] stopped: Spotify rate limit, Retry-After %.0fs",
                        job_id, rate_limited.retry_after_s)
            return
        _update_job(job_id, status="cancelled" if cancelled else "complete", current_track=None)
    except Exception as e:
        log.exception("playlist-url job crashed: %s", e)
        _update_job(job_id, status="error", error_message=f"{e.__class__.__name__}: {e}")
