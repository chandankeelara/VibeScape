import glob
import json
import os
import shutil
import socket
import tempfile
import threading
import time as _time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

# First: loads .env, imports backend/db.py, then puts the repo root and
# ingest/ on sys.path, in that order (see its docstring).
import bootstrap
from bootstrap import AUDIO_DIR, log

import numpy as np
import requests
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

# Through the module, never `from db import get_conn`: db.get_conn is the one
# seam tests patch (conftest.use_conn), and a copied name would not see it.
import db
from deps import require_user  # auth dependency
import same_song as _same_song
from dj_replay import library_mean as _dj_mean_of
from dj_replay import parse_events as _dj_parse_events
from dj_replay import replay as _dj_replay
import playlist_import as _pli
from track_rows import _row_to_dict, _search_rows, _track_select
from services.vectors import (FUSED_COLUMN, FUSED_V1_AVOID, FUSED_V1_MODEL_VERSION,
                              _DJ_VARIANT_DEFAULT, _embedding_column_for, _l2_normalize,
                              _load_mert_vec, _load_mert_vecs_bulk, _resolve_anchor,
                              _variant_spec)
from services.recency import (_RecencyCandidate, _UTS_JOIN, _UTS_JOIN_BY_USER, _UTS_SELECT,
                              _dj_recency_enabled, _dj_recency_pool, _fetch_recency_stats,
                              _fetch_user_halflife, _recency_penalty, _rerank_by_recency)
from services.jobs import JOBS, JOBS_LOCK, _bump, _is_cancelled, _update_job
# _get_app_token is patched by tests: read as spotify_auth._get_app_token.
from services import spotify_auth
# _process_track is patched by tests: read as library_ingest._process_track.
from services import library_ingest
from routers import admin as admin_routes
from routers import auth as auth_routes
from routers import events as events_routes
from routers import me as me_routes
from routers import meta as meta_routes
from routers import register as register_routes
from routers import spotify as spotify_routes
from routers import stream as stream_routes
from routers import tracks as tracks_routes
from routers import youtube as youtube_routes

import spotify_guard  # noqa: E402  — every api.spotify.com call goes through it
import spotify_library as splib  # noqa: E402

# `features` pulls in librosa/numpy — heavy deps not needed in the
# playback-only prod deployment. Import lazily at call sites that need it
# (only the ingest hot path, and only when Modal ML backend is unavailable).
feat = None

app = FastAPI(title="VibeScape API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers first, then the routes app.py still defines, then the SPA
# catch-all at the bottom, which must stay last or it swallows every API
# route. register(), not app.include_router: see routers/__init__.py.
register_routes(app, auth_routes.router)
register_routes(app, meta_routes.router)
register_routes(app, tracks_routes.router)
register_routes(app, spotify_routes.router)
register_routes(app, admin_routes.router)
register_routes(app, youtube_routes.router)
register_routes(app, stream_routes.router)
register_routes(app, events_routes.router)
register_routes(app, me_routes.router)


@app.on_event("startup")
def _startup():
    db.ensure_db()
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    _log_lan_bind_info()


def _lan_ip() -> Optional[str]:
    """Best-effort local LAN IP so the operator knows where phones should point."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return None


def _log_lan_bind_info() -> None:
    ip = _lan_ip()
    port = os.environ.get("VIBESCAPE_PORT", "8000")
    log.info("VibeScape backend listening on http://0.0.0.0:%s", port)
    if ip:
        log.info("LAN URL (share with phones on same WiFi): http://%s:%s/", ip, port)


# Server-side caps on client-supplied id lists. The frontend already trims
# (DJ_MAX_SENT_IDS / DJ_MAX_EXCLUDES in frontend-next/src/features/queue/dj.js),
# but a request body is user input: without these a large list means one
# embedding row loaded per positive/negative id, and an arbitrarily long
# inlined NOT IN (...) clause for excludes.
_DJ_MAX_WEIGHTED_IDS = 50
_DJ_MAX_EXCLUDE_IDS = 200


class SimilarBody(BaseModel):
    mode: Optional[str] = None
    positive_ids: Optional[list] = None
    negative_ids: Optional[list] = None
    # DJ replay log, oldest first: [{id, action, played_ratio, ts}]. When
    # present it replaces positive_ids / negative_ids entirely — see
    # backend/dj_replay.py. [frontend contract] new optional field.
    events: Optional[list] = None
    exclude_ids: Optional[list] = None
    limit: Optional[int] = None
    # "fused" (default) | "mert". Selects which embedding variant DJ mode uses.
    variant: Optional[str] = None
    # Opt-in per-candidate recency breakdown on each track. Additive and off
    # by default, so no existing client sees a change.
    # [frontend contract] — new optional request field, new optional response
    # field `explain` on each track, plus `final_score` alongside `score`.
    explain: Optional[bool] = False
    # Discovery mode: rank the whole catalogue, not only the caller's
    # library; each track then says whether it is in the library and
    # whether it is a discovery (_dj_tag_library). Off: unchanged.
    # [frontend contract] new optional request field.
    discover: Optional[bool] = False


def _similar_vibe(track_key: str, limit: int, user_id,
                  display_name=None, explain: bool = False, discover: bool = False):
    """Existing weighted L1 scalar-feature similarity. Returns response dict.

    Recency re-ranked on the same terms as DJ mode, and that is deliberate:
    _similar_dj falls back here whenever the seed has no vector in either
    variant, which is exactly the moment the library is thinnest and repeats
    are most likely. If the penalty existed only on the DJ path it would
    silently disappear at the worst possible time, with `mode_used` as the
    only evidence.

    This path has no embedding at all. It ranks by a weighted-L1 `distance`
    computed in SQL, roughly 0-3.3 and ASCENDING, so it feeds the shared
    re-ranker `score = -distance`. Because lambda is window-relative it needs
    no special-casing -- but lambda here is far larger in absolute terms than
    on the cosine path. That is correct. Do not compare the two.
    """
    conn = db.get_conn()
    try:
        anchor = _resolve_anchor(conn, track_key)
        if not anchor:
            raise HTTPException(status_code=404, detail={"error": "track_not_found"})

        # Anchor features. If ML predictions haven't run for this track yet,
        # fall back to the formula vibe (0-100 scaled to 0-1) so we still
        # get sensible ordering. Missing prediction cols become 0.5 (neutral).
        def _norm(v, default):
            if v is None:
                return default
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        a_vibe = _norm(anchor["vibe_score_ml"], _norm(anchor["vibe_score"], 50.0) / 100.0)
        a_energy = _norm(anchor["energy_pred"], 0.5)
        a_dance = _norm(anchor["danceability_pred"], 0.5)
        a_valence = _norm(anchor["valence_pred"], 0.5)
        a_mood = anchor["mood"] or ""

        # Weighted L1 in SQL. Weights emphasise the ML vibe (most user-visible
        # signal), then energy/danceability, then valence. Missing prediction
        # columns coalesce to 0.5 so those rows land in the middle of the
        # ranking rather than being excluded entirely. Mood match gives a
        # discount of 0.15 (roughly one dimension's worth of distance) to
        # nudge same-mood tracks up the list.
        select = _track_select(conn)
        rerank = _dj_recency_enabled(display_name)
        # Kill switch / guest opt-out: emit exactly the pre-feature SQL.
        stats_select = (",\n" + _UTS_SELECT) if rerank else ""
        stats_join = ("              " + (_UTS_JOIN_BY_USER if discover else _UTS_JOIN)
                      + "\n") if rerank else ""
        lib_join = "" if discover else "            JOIN user_tracks ut ON ut.track_id = t.id\n"
        lib_where = "" if discover else "ut.user_id = ?\n              AND "
        fetch = _dj_recency_pool(limit) if rerank else limit
        sql = f"""
            SELECT {select},
              (
                1.5 * ABS(COALESCE(t.vibe_score_ml,        {a_vibe})    - {a_vibe})
              + 1.0 * ABS(COALESCE(t.energy_pred,         0.5)          - {a_energy})
              + 1.0 * ABS(COALESCE(t.danceability_pred,   0.5)          - {a_dance})
              + 0.8 * ABS(COALESCE(t.valence_pred,        0.5)          - {a_valence})
              - CASE WHEN t.mood = ? THEN 0.15 ELSE 0.0 END
              ) AS distance{stats_select}
            FROM tracks t
{lib_join}{stats_join}            WHERE {lib_where}t.ingestion_status = 'done'
              AND t.id != ?
            ORDER BY distance ASC, t.title COLLATE NOCASE
            LIMIT ?
        """
        # The user id binds once: in the WHERE, or (discovery, re-ranked) in
        # the stats join; discovery without the re-rank has neither.
        uid_args = () if discover and not rerank else (user_id,)
        rows = conn.execute(sql, (a_mood, *uid_args, anchor["id"], fetch)).fetchall()
        anchor_out = {
            "spotify_id": anchor["spotify_id"],
            "apple_id": anchor["apple_id"],
            "mood": a_mood,
        }
        h_user = _fetch_user_halflife(conn, user_id) if rerank else None
    finally:
        conn.close()

    if not rerank:
        return {
            "anchor": anchor_out,
            "tracks": [_row_to_dict(r) for r in rows[:limit]],
            "mode_used": "vibe",
        }

    cands = []
    for r in rows:
        dist = r["distance"]
        cands.append(_RecencyCandidate(
            score=-(float(dist) if dist is not None else 999.0),
            payload=r,
            last_played=r["uts_last_played"],
            last_skipped_at=r["uts_last_skipped_at"],
            skip_count=r["uts_skip_count"],
            skip_pos_sum=r["uts_skip_pos_sum"],
            duration_ms=r["duration_ms"],
        ))

    out_tracks = []
    for c, final, block in _rerank_by_recency(cands, limit, explain=explain,
                                              halflife_h=h_user):
        d = _row_to_dict(c.payload)
        d["final_score"] = final
        if block is not None:
            d["explain"] = block
        out_tracks.append(d)

    return {
        "anchor": anchor_out,
        "tracks": out_tracks,
        "mode_used": "vibe",
    }


def _parse_id_weight_list(items) -> list:
    """Normalize [{id, weight}] entries. Returns list of (tid, float_weight).

    Preserves int types (frontend hot path sends internal tracks.id as int
    to skip resolution). Strings are stringified; downstream batch resolver
    handles both.
    """
    out = []
    if not items:
        return out
    for it in items:
        if isinstance(it, dict):
            tid = it.get("id")
            w = it.get("weight", 1.0)
        else:
            tid, w = it, 1.0
        if tid is None:
            continue
        try:
            w = float(w)
        except (TypeError, ValueError):
            w = 1.0
        # Keep int as int; everything else becomes a string. The batch
        # resolver distinguishes on isinstance(k, int).
        if not isinstance(tid, int):
            tid = str(tid)
        out.append((tid, w))
    return out


def _resolve_ids_to_track_ids(conn, keys) -> list:
    """Batch-resolve a mixed list of track keys to internal tracks.id ints.

    Each entry may be:
      * int → already an internal id, kept as-is
      * str parseable as int → treated as an internal id (frontend sends
        ints; JSON parsers may deliver them as either)
      * str spotify_id → resolved via a single batched SELECT

    Callers previously looped `_resolve_anchor` per key, paying one DB
    round-trip each. On a 50-entry exclude list against Cloud Run → Turso
    that added ~750 ms of pure overhead. This helper collapses that to at
    most one round-trip regardless of list size.
    """
    ids: list = []
    spotify_keys: list = []
    for k in keys or []:
        if k is None:
            continue
        if isinstance(k, int):
            ids.append(k)
            continue
        s = str(k)
        # Numeric string → internal id. Spotify IDs are base62 (letters+
        # digits, 22 chars) so an all-digit key is unambiguous.
        if s.isdigit():
            try:
                ids.append(int(s))
                continue
            except ValueError:
                pass
        spotify_keys.append(s)
    if spotify_keys:
        # Single SQL round-trip. `?` placeholders scale fine into the
        # low thousands on sqlite/libsql — well past any realistic DJ
        # exclude buffer.
        placeholders = ",".join("?" * len(spotify_keys))
        rows = conn.execute(
            f"SELECT id FROM tracks WHERE spotify_id IN ({placeholders})",
            spotify_keys,
        ).fetchall()
        ids.extend(int(r["id"]) for r in rows)
    return ids


# ---------------------------------------------------------------------------
# DJ replay: per-user library mean.
#
# dj_replay centres every vector on the mean of the user's analysed library
# before replaying (the fused space is a narrow cone — see its docstring).
# The mean is a cache in user_embedding_mean, keyed (user_id, model_version),
# stored as JSON text because Turso BLOB reads can come back empty. It is
# rebuilt when the live count of analysed library tracks drifts by more than
# _DJ_MEAN_DRIFT from the count it was built on. Libraries under
# _DJ_MEAN_MIN_TRACKS use the all-tracks mean, stored as user_id 0.
#
# Per-user vs global made no measurable difference offline (AUC 0.828 vs
# 0.830, 2026-10-10), but a 343-track library's mean was 0.97 cosine from
# the global one, and the gap grows with how unusual a library is.
# ---------------------------------------------------------------------------
_DJ_MEAN_MIN_TRACKS = 50
_DJ_MEAN_DRIFT = 0.05
_DJ_MEAN_RECHECK_S = 600.0
_dj_mean_cache: dict = {}  # (user_id, model_version) -> (n, mean, checked_at, scope_uid)


def _dj_count_embedded(conn, scope_uid: int, emb_col: str) -> int:
    if scope_uid:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM user_tracks ut "
            f"JOIN tracks t ON t.id = ut.track_id "
            f"JOIN track_embeddings te ON te.track_id = t.id "
            f"WHERE ut.user_id = ? AND t.ingestion_status = 'done' "
            f"AND te.{emb_col} IS NOT NULL", (scope_uid,)).fetchone()
    else:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM track_embeddings te "
            f"JOIN tracks t ON t.id = te.track_id "
            f"WHERE t.ingestion_status = 'done' AND te.{emb_col} IS NOT NULL").fetchone()
    return int(row["n"] or 0)


def _dj_library_mean(conn, user_id, model_version: str, dim: int):
    """Mean of unit vectors over the user's analysed library, or None."""
    emb_col = _embedding_column_for(model_version)
    if emb_col is None:
        return None
    now = datetime.now(timezone.utc).timestamp()

    def fresh(n_built, n_live):
        return n_built > 0 and abs(n_live - n_built) <= _DJ_MEAN_DRIFT * n_built

    # Cached per REQUESTING user: a small library resolves to the global
    # mean, a large one to its own, and neither may answer for the other.
    req_key = (int(user_id or 0), model_version)
    hit = _dj_mean_cache.get(req_key)
    if hit and now - hit[2] < _DJ_MEAN_RECHECK_S:
        return hit[1]

    scope = int(user_id or 0)
    live = _dj_count_embedded(conn, scope, emb_col) if scope else 0
    if live < _DJ_MEAN_MIN_TRACKS:
        scope = 0
        live = _dj_count_embedded(conn, 0, emb_col)
    if live == 0:
        return None

    if hit and hit[3] == scope and fresh(hit[0], live):
        _dj_mean_cache[req_key] = (hit[0], hit[1], now, scope)
        return hit[1]

    try:
        row = conn.execute(
            "SELECT n_tracks, mean_json FROM user_embedding_mean "
            "WHERE user_id = ? AND model_version = ?", (scope, model_version)).fetchone()
    except Exception as e:  # table not created yet (Turso needs the one-shot script)
        log.warning("[dj] user_embedding_mean unreadable (%s); computing in-process", e)
        row = None
    if row is not None and fresh(int(row["n_tracks"]), live):
        try:
            mean = np.array(json.loads(row["mean_json"]), dtype=np.float64)
            if mean.shape[0] == dim:
                _dj_mean_cache[req_key] = (int(row["n_tracks"]), mean, now, scope)
                return mean
        except (TypeError, ValueError):
            pass

    # Rebuild: one pass over the library's vectors. On Turso this is the
    # expensive read, which is why the result is stored, not recomputed.
    if scope:
        ids = [int(r["id"]) for r in conn.execute(
            f"SELECT t.id AS id FROM user_tracks ut JOIN tracks t ON t.id = ut.track_id "
            f"JOIN track_embeddings te ON te.track_id = t.id "
            f"WHERE ut.user_id = ? AND t.ingestion_status = 'done' "
            f"AND te.{emb_col} IS NOT NULL", (scope,)).fetchall()]
    else:
        ids = [int(r["id"]) for r in conn.execute(
            f"SELECT t.id AS id FROM track_embeddings te JOIN tracks t ON t.id = te.track_id "
            f"WHERE t.ingestion_status = 'done' AND te.{emb_col} IS NOT NULL").fetchall()]
    vecs = _load_mert_vecs_bulk(conn, ids, model_version, dim)
    mean = _dj_mean_of(vecs.values())
    if mean is None:
        return None
    n = len(vecs)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_embedding_mean "
            "(user_id, model_version, n_tracks, mean_json, computed_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (scope, model_version, n, json.dumps([round(float(x), 7) for x in mean])))
        conn.commit()
    except Exception as e:
        log.warning("[dj] could not store user_embedding_mean (%s); kept in memory", e)
    _dj_mean_cache[req_key] = (n, mean, now, scope)
    return mean


def _dj_events_query(conn, raw_events, user_id, seed_vec, model_version: str, dim: int,
                     seed_id=None):
    """Resolve the client's event log and replay it. Returns (query, result,
    session song keys): the same_song keys of every track in the log, and of
    the seed, which _dj_drop_repeats keeps out of the response."""
    events = _dj_parse_events(raw_events)
    str_keys = sorted({e["id"] for e in events
                       if isinstance(e["id"], str) and not e["id"].isdigit()})
    by_spotify = {}
    if str_keys:
        ph = ",".join("?" * len(str_keys))
        for r in conn.execute(f"SELECT id, spotify_id FROM tracks WHERE spotify_id IN ({ph})",
                              str_keys).fetchall():
            by_spotify[r["spotify_id"]] = int(r["id"])
    resolved = []
    for e in events:
        k = e["id"]
        tid = k if isinstance(k, int) else (int(k) if k.isdigit() else by_spotify.get(k))
        if tid is not None:
            resolved.append({**e, "id": tid})
    vecs = _load_mert_vecs_bulk(conn, sorted({e["id"] for e in resolved}), model_version, dim)
    mu = _dj_library_mean(conn, user_id, model_version, dim)
    if mu is None:
        mu = np.zeros(dim, dtype=np.float64)
    song_keys = _dj_song_keys(conn, {e["id"] for e in resolved} | ({seed_id} if seed_id else set()))
    avoid = FUSED_V1_AVOID if model_version == FUSED_V1_MODEL_VERSION else {}
    res = _dj_replay(resolved, vecs, mu, seed_vec, song_keys=song_keys, **avoid)
    q = res.query.astype(np.float32) if res.query is not None else None
    return q, res, {k for k in song_keys.values() if k is not None}


def _dj_song_keys(conn, ids) -> dict:
    """{track_id: same_song.song_key} for `ids`, one query."""
    ids = sorted(int(i) for i in ids)
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    return {int(r["id"]): _same_song.song_key(r["title"], r["artist"], r["language"])
            for r in conn.execute(f"SELECT id, title, artist, language FROM tracks "
                                  f"WHERE id IN ({ph})", ids).fetchall()}


def _dj_drop_repeats(items, key_of, session_keys):
    """`items` (best first) without another release of a song the session
    already had, nor a second release of a song kept earlier in the list:
    the DJ served Belakina Kavithe's album and single releases back to back,
    and Mask Off right after its remix. Both ranking paths call this on
    their candidate pool, before the recency re-rank and the cut to
    `limit`, so the response still has `limit` tracks when the pool has."""
    seen, out = set(session_keys), []
    for it in items:
        k = key_of(it)
        if k is not None:
            if k in seen:
                continue
            seen.add(k)
        out.append(it)
    return out


def _similar_dj(track_key: str, body: SimilarBody, user_id, display_name=None):
    """Cosine similarity over per-track embedding vectors. Variant selects
    which embedding table row to use ('fused' = MERT + scalars + language,
    'mert' = raw MERT). Falls back cross-variant, then to vibe.

    Results are recency re-ranked -- see the _recency_penalty block above for
    the design and the three properties it must keep. `score` still means
    cosine similarity and nothing else; the post-penalty value is reported
    separately as `final_score`. Redefining an existing numeric field in place
    is backlog item 1.2's exact failure mode.
    """
    limit = body.limit if body.limit else 8
    limit = max(1, min(int(limit), 25))
    rerank = _dj_recency_enabled(display_name)
    explain = bool(getattr(body, "explain", False))
    # Discovery ranks the whole catalogue with the same query, re-ranks and
    # pool; only the user_tracks restriction goes. The query is still
    # centred on the caller's library mean. A track the caller never played
    # carries no recency penalty, which leans the top K slightly toward
    # discoveries; nothing compensates for that, on purpose.
    discover = bool(getattr(body, "discover", False))
    pool = _dj_recency_pool(limit) if rerank else limit

    requested_variant = (body.variant or _DJ_VARIANT_DEFAULT).strip().lower()
    if requested_variant not in ("fused", "mert"):
        requested_variant = _DJ_VARIANT_DEFAULT

    conn = db.get_conn()
    try:
        anchor = _resolve_anchor(conn, track_key)
    except Exception:
        conn.close()
        raise
    if not anchor:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "track_not_found"})
    anchor_id = int(anchor["id"])
    a_mood = anchor["mood"] or ""
    # One SELECT, done up front so it is shared across the Turso and numpy
    # branches below. None when the gate is off -- the re-ranker treats that
    # as "use the env default".
    h_user = _fetch_user_halflife(conn, user_id) if rerank else None

    # Resolve variant with cross-variant fallback: try requested; if seed
    # has no vector there, try the other variant; if neither, drop to vibe.
    model_version, dim = _variant_spec(requested_variant, conn)
    seed_vec = _load_mert_vec(conn, anchor_id, model_version, dim)
    variant_used = requested_variant

    if seed_vec is None:
        fallback_variant = "mert" if requested_variant == "fused" else "fused"
        fb_model_version, fb_dim = _variant_spec(fallback_variant, conn)
        seed_vec = _load_mert_vec(conn, anchor_id, fb_model_version, fb_dim)
        if seed_vec is not None:
            model_version, dim = fb_model_version, fb_dim
            variant_used = fallback_variant

    # With an event log the seed only matters for cold start, so a seed with
    # no vector (pending ingest — e.g. a just-searched track) no longer
    # throws the whole session away.
    use_events = body.events is not None
    replay_res = None
    session_keys = None

    if seed_vec is None and not use_events:
        conn.close()
        resp = _similar_vibe(track_key, limit, user_id,
                             display_name=display_name, explain=explain, discover=discover)
        resp["mode_used"] = "vibe_fallback_no_seed_embedding"
        resp["variant_used"] = None
        return resp

    try:
        if use_events:
            # Replay path (backend/dj_replay.py): the client sends its raw
            # event log and every bit of weighting happens here. The seed's
            # OWN events count — a search-and-play is the seed, and dropping
            # it (as the weighted path below does) threw away the strongest
            # signal there is. The seed is still excluded from candidates.
            query_vec, replay_res, session_keys = _dj_events_query(
                conn, body.events, user_id, seed_vec, model_version, dim, seed_id=anchor_id)
            # Repeats are filtered out of a pool, so the pool must be deeper
            # than `limit` even with the recency re-rank off.
            pool = max(pool, _dj_recency_pool(limit))
            if query_vec is None:
                # No usable event AND a seed with no vector: nothing to
                # point a query at. Same fallback as a seed without a vector.
                resp = _similar_vibe(track_key, limit, user_id,
                                     display_name=display_name, explain=explain,
                                     discover=discover)
                resp["mode_used"] = "vibe_fallback_no_signal"
                resp["variant_used"] = None
                return resp
        else:
            # Legacy weighted path, kept byte-for-byte for clients that still
            # send positive_ids / negative_ids.
            # Resolve positive/negative track_keys to internal ids. The seed
            # track_key in the URL is used only for exclusion — it does NOT
            # contribute to the query vector. Recommendations are driven purely
            # by the user's session (completions, skips, queue-adds).
            # Strongest weights win when a client sends more than we'll accept.
            def _cap_pairs(pairs):
                if len(pairs) <= _DJ_MAX_WEIGHTED_IDS:
                    return pairs
                return sorted(pairs, key=lambda kw: kw[1], reverse=True)[:_DJ_MAX_WEIGHTED_IDS]

            pos_pairs = _cap_pairs(_parse_id_weight_list(body.positive_ids))
            neg_pairs = _cap_pairs(_parse_id_weight_list(body.negative_ids))

            def _resolve_pairs(pairs):
                """Batch-resolve (key, weight) pairs to (internal_id, weight).

                Splits int/int-string entries (already resolved) from spotify_id
                strings (need lookup), then issues a single SQL for the strings.
                Preserves weights via a key→weight map so we don't lose data on
                the batch round-trip.
                """
                if not pairs:
                    return []
                resolved: list = []
                str_keys: list = []
                str_weight: dict = {}
                for k, w in pairs:
                    if isinstance(k, int):
                        resolved.append((k, w))
                        continue
                    s = str(k)
                    if s.isdigit():
                        try:
                            resolved.append((int(s), w))
                            continue
                        except ValueError:
                            pass
                    str_keys.append(s)
                    # If the same spotify_id appears twice we keep the last
                    # weight — callers don't emit duplicates today.
                    str_weight[s] = w
                if str_keys:
                    placeholders = ",".join("?" * len(str_keys))
                    rows = conn.execute(
                        f"SELECT id, spotify_id FROM tracks "
                        f"WHERE spotify_id IN ({placeholders})",
                        str_keys,
                    ).fetchall()
                    for r in rows:
                        tid = int(r["id"])
                        w = str_weight.get(r["spotify_id"], 1.0)
                        resolved.append((tid, w))
                return resolved

            pos_id_w = [(tid, w) for (tid, w) in _resolve_pairs(pos_pairs) if tid != anchor_id]
            neg_id_w = [(tid, w) for (tid, w) in _resolve_pairs(neg_pairs) if tid != anchor_id]

            needed_ids = list({tid for tid, _ in pos_id_w + neg_id_w})
            ctx_vecs = _load_mert_vecs_bulk(conn, needed_ids, model_version, dim)

            def _combine(pairs):
                acc = np.zeros(dim, dtype=np.float32)
                for tid, w in pairs:
                    v = ctx_vecs.get(tid)
                    if v is None:
                        continue
                    acc = acc + (w * _l2_normalize(v))
                return acc

            pos_vec = _combine(pos_id_w)
            neg_vec = _combine(neg_id_w)

            pos_norm = float(np.linalg.norm(pos_vec))
            taste_present = pos_norm >= 0.1

            query_vec = None
            if taste_present:
                # Pure session taste. Answers "what's next for me?"
                query_vec = _l2_normalize(pos_vec - 0.4 * neg_vec)
            # Cold start (no session signal): query_vec stays None; we'll pick
            # a random slice of the candidate pool below.

        # Build exclude set: request excludes + seed itself.
        # Truncated, not rejected: excludes are a nicety (avoid replaying
        # something recent), so dropping the tail degrades gracefully.
        exclude_keys = [str(x) for x in (body.exclude_ids or []) if x is not None]
        exclude_keys = exclude_keys[:_DJ_MAX_EXCLUDE_IDS]
        exclude_ids = set(_resolve_ids_to_track_ids(conn, exclude_keys))
        exclude_ids.add(anchor_id)

        # Load candidate track_ids from user's library (done + has the
        # requested embedding variant, not excluded). Option A layout:
        # one row per track, each variant in its own typed column.
        emb_col = _embedding_column_for(model_version) or FUSED_COLUMN
        mode_used = f"dj_replay_{variant_used}" if use_events else f"dj_{variant_used}"
        if replay_res is not None and replay_res.from_seed:
            mode_used += "_from_seed"

        # ------------------------------------------------------------------
        # Ranking backend: on Turso we push the cosine computation into the
        # DB (vector_distance_cos on the typed column) so we only stream
        # top-K + metadata back — ~10 KB per request instead of 4.7 MB of
        # embedding blobs. On sqlite we fall back to the in-process numpy
        # brute-force since vanilla sqlite has no vector functions.
        # ------------------------------------------------------------------
        _use_turso_ranking = (
            (os.environ.get("DB_BACKEND") or "").strip().lower() in ("turso", "libsql")
            and query_vec is not None
        )

        top_ids: list[int] = []
        top_scores: dict[int, float] = {}
        out_tracks: list[dict] = []

        if _use_turso_ranking:
            # Serialize query vector for Turso's vector32('[…]') builder.
            qv_str = "[" + ",".join(f"{float(x):.7f}" for x in query_vec.tolist()) + "]"
            # Exclude filter — inlined as literal ints since sqlite param
            # counts have limits and this list is bounded (~50-100).
            excl_sql = ""
            if exclude_ids:
                excl_sql = " AND t.id NOT IN ("
                excl_sql += ",".join(str(int(x)) for x in exclude_ids)
                excl_sql += ") "
            select = _track_select(conn)
            # Zero extra round trips: the stats arrive on a LEFT JOIN added to
            # the query we were already issuing. With the kill switch set,
            # neither the join nor the wider LIMIT is emitted at all.
            stats_select = (", " + _UTS_SELECT.replace("\n", " ")) if rerank else ""
            stats_join = ((_UTS_JOIN_BY_USER if discover else _UTS_JOIN) + " ") if rerank else ""
            lib_join = "" if discover else "JOIN user_tracks ut ON ut.track_id = t.id "
            lib_where = "" if discover else "ut.user_id = ? AND "
            sql = (
                f"SELECT {select}, "
                f"       vector_distance_cos(te.{emb_col}, vector32(?)) AS distance"
                f"{stats_select} "
                f"FROM tracks t "
                f"{lib_join}"
                f"JOIN track_embeddings te ON te.track_id = t.id "
                f"{stats_join}"
                f"WHERE {lib_where}"
                f"  t.ingestion_status = 'done' "
                f"  AND te.{emb_col} IS NOT NULL "
                f"  {excl_sql} "
                f"ORDER BY distance ASC "
                f"LIMIT ?"
            )
            try:
                # The user id binds once, after the SELECT's vector: in the
                # WHERE, or (discovery, re-ranked) in the stats join.
                uid_args = () if discover and not rerank else (user_id,)
                rows = conn.execute(sql, (qv_str, *uid_args, pool)).fetchall()
            except Exception as e:
                log.warning("[dj] turso vector_distance_cos path failed: %s; "
                            "falling back to numpy", e)
                rows = None
            if rows is not None and session_keys is not None:
                rows = _dj_drop_repeats(
                    rows, lambda r: _same_song.song_key(r["title"], r["artist"], r["language"]),
                    session_keys)
            if rows is not None:
                # vector_distance_cos returns (1 - cos_sim), so cosine
                # similarity = 1 - distance. Frontend expects similarity.
                def _sim(r):
                    dist = float(r["distance"]) if r["distance"] is not None else 1.0
                    return 1.0 - dist

                if not rerank:
                    for r in rows[:limit]:
                        d = _row_to_dict(r)
                        d["score"] = _sim(r)
                        out_tracks.append(d)
                else:
                    cands = [
                        _RecencyCandidate(
                            score=_sim(r),
                            payload=r,
                            last_played=r["uts_last_played"],
                            last_skipped_at=r["uts_last_skipped_at"],
                            skip_count=r["uts_skip_count"],
                            skip_pos_sum=r["uts_skip_pos_sum"],
                            duration_ms=r["duration_ms"],
                        )
                        for r in rows
                    ]
                    for c, final, block in _rerank_by_recency(
                            cands, limit, explain=explain,
                            halflife_h=h_user):
                        d = _row_to_dict(c.payload)
                        d["score"] = c.score
                        d["final_score"] = final
                        if block is not None:
                            d["explain"] = block
                        out_tracks.append(d)
                anchor_out = {
                    "spotify_id": anchor["spotify_id"],
                    "apple_id": anchor["apple_id"],
                    "mood": a_mood,
                }
                return {
                    "anchor": anchor_out,
                    "tracks": out_tracks,
                    "mode_used": mode_used + "_turso",
                    "variant_used": variant_used,
                }
            # else: fall through to numpy path

        # -------- numpy fallback (local dev / cold-start / turso error) --------
        if discover:
            cand_rows = conn.execute(
                "SELECT t.id, t.duration_ms, t.title, t.artist, t.language FROM tracks t "
                "JOIN track_embeddings te ON te.track_id = t.id "
                "WHERE t.ingestion_status = 'done' "
                f" AND te.{emb_col} IS NOT NULL",
            ).fetchall()
        else:
            cand_rows = conn.execute(
                "SELECT t.id, t.duration_ms, t.title, t.artist, t.language FROM tracks t "
                "JOIN user_tracks ut ON ut.track_id = t.id "
                "JOIN track_embeddings te ON te.track_id = t.id "
                "WHERE ut.user_id = ? "
                "  AND t.ingestion_status = 'done' "
                f" AND te.{emb_col} IS NOT NULL",
                (user_id,),
            ).fetchall()
        # duration_ms rides along on the query we were already issuing; it is
        # the denominator of the skip-depth term and the full track rows are
        # not fetched until after re-ranking on this path.
        cand_duration = {int(r["id"]): r["duration_ms"] for r in cand_rows}
        cand_key = ({int(r["id"]): _same_song.song_key(r["title"], r["artist"], r["language"])
                     for r in cand_rows} if session_keys is not None else {})
        cand_ids = [int(r["id"]) for r in cand_rows if int(r["id"]) not in exclude_ids]
        if not cand_ids:
            anchor_out = {
                "spotify_id": anchor["spotify_id"],
                "apple_id": anchor["apple_id"],
                "mood": a_mood,
            }
            return {"anchor": anchor_out, "tracks": [],
                    "mode_used": mode_used, "variant_used": variant_used}

        cand_vecs = _load_mert_vecs_bulk(conn, cand_ids, model_version, dim)
        if not cand_vecs:
            anchor_out = {
                "spotify_id": anchor["spotify_id"],
                "apple_id": anchor["apple_id"],
                "mood": a_mood,
            }
            return {"anchor": anchor_out, "tracks": [],
                    "mode_used": mode_used, "variant_used": variant_used}

        ids_arr = np.array(list(cand_vecs.keys()), dtype=np.int64)

        # One extra query on this path, and only on this path -- there is no
        # candidate query here to hang the LEFT JOIN off. Bounded by "tracks
        # this user touched in 30 days", not by library size.
        stats = _fetch_recency_stats(conn, user_id) if rerank else {}

        def _mk_cand(tid, score):
            st = stats.get(tid)
            return _RecencyCandidate(
                score=score, payload=tid,
                last_played=st[0] if st else None,
                last_skipped_at=st[1] if st else None,
                skip_count=st[2] if st else 0,
                skip_pos_sum=st[3] if st else 0,
                duration_ms=cand_duration.get(tid),
            )

        explains: dict = {}
        finals: dict = {}
        if query_vec is None:
            # Cold start: no similarity signal to trade against, so there is
            # no lambda to calibrate and no window to measure. Keep it random
            # -- cold start must not be deterministic -- but weight the draw
            # by 1/(1+P), which makes a track played an hour ago about half as
            # likely as one never heard. Still one-sided: P only ever lowers
            # a track's weight.
            rng = np.random.default_rng()
            take = min(limit, len(ids_arr))
            if rerank and stats:
                now = datetime.now(timezone.utc)
                w = np.empty(len(ids_arr), dtype=np.float64)
                for i, tid in enumerate(ids_arr):
                    c = _mk_cand(int(tid), 0.0)
                    p, _ex = _recency_penalty(
                        now, c.last_played, c.last_skipped_at,
                        c.skip_count, c.skip_pos_sum, c.duration_ms,
                        halflife_h=h_user)
                    w[i] = 1.0 / (1.0 + p)
                total = float(w.sum())
                probs = (w / total) if total > 0 else None
                pick = rng.choice(len(ids_arr), size=take, replace=False, p=probs)
            else:
                pick = rng.permutation(len(ids_arr))[:take]
            top_ids = [int(ids_arr[i]) for i in pick]
            top_scores = {tid: 0.0 for tid in top_ids}
        else:
            mat = np.stack([cand_vecs[int(i)] for i in ids_arr]).astype(np.float32)
            # Row-normalize.
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms < 1e-12] = 1.0
            mat_n = mat / norms
            sims = mat_n @ query_vec.astype(np.float32)
            ranked = None
            if session_keys is not None:
                ranked = _dj_drop_repeats([int(i) for i in np.argsort(-sims)[:pool]],
                                          lambda i: cand_key.get(int(ids_arr[i])), session_keys)
            if not rerank:
                order = ranked[:limit] if ranked is not None else np.argsort(-sims)[:limit]
                top_ids = [int(ids_arr[i]) for i in order]
                top_scores = {int(ids_arr[i]): float(sims[i]) for i in order}
            else:
                # Pool, not the whole library, so this path displaces ranks by
                # the same amount the Turso path does -- otherwise local
                # behaviour would not predict production's.
                order = ranked if ranked is not None else np.argsort(-sims)[:pool]
                cands = [_mk_cand(int(ids_arr[i]), float(sims[i])) for i in order]
                top_ids = []
                top_scores = {}
                for c, final, block in _rerank_by_recency(
                        cands, limit, explain=explain,
                        halflife_h=h_user):
                    tid = int(c.payload)
                    top_ids.append(tid)
                    top_scores[tid] = c.score
                    finals[tid] = final
                    if block is not None:
                        explains[tid] = block

        # Fetch full track rows for the top ids, preserve order.
        select = _track_select(conn)
        placeholders = ",".join("?" for _ in top_ids)
        rows = conn.execute(
            f"SELECT {select} FROM tracks t WHERE t.id IN ({placeholders})",
            tuple(top_ids),
        ).fetchall()
        row_by_id = {int(r["id"]): r for r in rows}
        out_tracks = []
        for tid in top_ids:
            r = row_by_id.get(tid)
            if not r:
                continue
            d = _row_to_dict(r)
            d["score"] = top_scores.get(tid, 0.0)
            if tid in finals:
                d["final_score"] = finals[tid]
            if tid in explains:
                d["explain"] = explains[tid]
            out_tracks.append(d)

        anchor_out = {
            "spotify_id": anchor["spotify_id"],
            "apple_id": anchor["apple_id"],
            "mood": a_mood,
        }
        return {"anchor": anchor_out, "tracks": out_tracks,
                "mode_used": mode_used, "variant_used": variant_used}
    finally:
        conn.close()


@app.get("/api/tracks/{track_key}/similar")
def similar_tracks(
    track_key: str,
    limit: int = Query(8, ge=1, le=25),
    discover: bool = False,
    sess: dict = Depends(require_user),
):
    """
    Return N tracks from the caller's library most similar to the given
    track. Similarity = weighted L1 distance across the four ML feature
    dimensions (vibe_score_ml, energy_pred, danceability_pred,
    valence_pred), with a small bonus for matching mood. The current
    track is excluded from results.

    track_key can be a spotify_id (string) or a numeric internal tracks.id.
    `discover=1` ranks the whole catalogue instead (see SimilarBody).
    """
    resp = _similar_vibe(track_key, limit, sess["user_id"],
                         display_name=sess.get("display_name"), discover=discover)
    return _discover_finish(resp, sess["user_id"]) if discover else resp


@app.post("/api/tracks/{track_key}/similar")
def similar_tracks_post(
    track_key: str,
    body: Optional[SimilarBody] = None,
    sess: dict = Depends(require_user),
):
    """POST variant: supports DJ mode (fused MERT cosine) via JSON body.

    Body (all optional):
      mode: "dj" | "vibe" (default vibe)
      positive_ids: [{id, weight}, ...] — id may be internal tracks.id (int)
        or spotify_id (str). Ints skip resolution; strings batch-resolve.
      negative_ids: [{id, weight}, ...] — same shape as positive_ids
      exclude_ids: [id, ...] — mixed list of internal tracks.id ints and/or
        spotify_id strings. Ints skip resolution.
      limit: int (1..25, default 8)
      explain: bool (default false) — attach a per-track `explain` block with
        the recency penalty breakdown and rank_before / rank_after. Opt-in;
        off by default so no existing client sees a change.
      discover: bool (default false) — rank the whole catalogue, not only the
        caller's library. Each track's `in_library` and `discover` are then
        real, and `mode_used` ends in `_discover`.

    Each track carries `score` (cosine similarity on the DJ path, absent on
    the vibe path — unchanged meaning) and, when recency re-ranking is
    active, `final_score` = score - lambda * P.
    """
    if body is None:
        body = SimilarBody()
    limit = body.limit if body.limit else 8
    limit = max(1, min(int(limit), 25))
    mode = (body.mode or "vibe").lower()
    discover = bool(body.discover)
    if mode == "dj":
        resp = _similar_dj(track_key, body, sess["user_id"],
                           display_name=sess.get("display_name"))
    else:
        resp = _similar_vibe(track_key, limit, sess["user_id"],
                             display_name=sess.get("display_name"),
                             explain=bool(body.explain), discover=discover)
    return _discover_finish(resp, sess["user_id"]) if discover else resp


def _dj_tag_library(conn, user_id, tracks) -> None:
    """Set `in_library` and `discover` on discovery-mode tracks, in place.

    in_library is this row's own link (what the heart's PUT / DELETE acts
    on): one query over the <= 25 returned ids, never a per-candidate
    EXISTS in the ranking scan. discover is "not in the library, and no
    library song shares its same_song key", so another release of a song
    the caller owns is neither: no tag, and an empty heart. The keys come
    from _search_rows, cached 60 s and dropped by the heart, so they cost
    no read on a warm cache. That cache holds analysed ('done') rows only:
    a library song still in the pipeline does not hide its other releases.
    """
    ids = sorted({int(t["id"]) for t in tracks})
    if not ids:
        return
    ph = ",".join("?" * len(ids))
    linked = {int(r[0]) for r in conn.execute(
        f"SELECT track_id FROM user_tracks WHERE user_id = ? AND track_id IN ({ph})",
        (user_id, *ids)).fetchall()}
    owned = {_same_song.song_key(r["title"], r["artist"], r["language"])
             for r in _search_rows(conn, user_id)}
    owned.discard(None)
    for t in tracks:
        t["in_library"] = int(t["id"]) in linked
        t["discover"] = (not t["in_library"]
                         and _same_song.song_key(t.get("title"), t.get("artist"),
                                                 t.get("language")) not in owned)


def _discover_finish(resp: dict, user_id) -> dict:
    """Discovery mode's response: real library tags on every track, and a
    `_discover` suffix on mode_used. At the endpoint, so the vibe fallbacks
    inside _similar_dj (which overwrite mode_used) get both too."""
    resp["mode_used"] = f"{resp.get('mode_used')}_discover"
    if resp.get("tracks"):
        conn = db.get_conn()
        try:
            _dj_tag_library(conn, user_id, resp["tracks"])
        finally:
            conn.close()
    return resp


# ---------------- Spotify library ingest ----------------


def _bearer_token(auth_header: Optional[str]) -> str:
    if not auth_header:
        raise HTTPException(status_code=401, detail={"error": "missing_authorization"})
    parts = auth_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail={"error": "invalid_authorization"})
    return parts[1].strip()


@app.post("/api/ingest/clear")
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


class IngestSources(BaseModel):
    liked: bool = False
    top_tracks: bool = False
    playlist_ids: list[str] = []


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


def _collect_tracks(token: str, sources: IngestSources) -> list[tuple[dict, str]]:
    seen: set[str] = set()
    out: list[tuple[dict, str]] = []

    def _add(items: list[dict], src: str):
        for t in items:
            tid = t.get("id") if isinstance(t, dict) else None
            if not tid or tid in seen:
                continue
            seen.add(tid)
            out.append((t, src))

    if sources.liked:
        _add(splib.fetch_liked(token), "liked_songs")
    if sources.top_tracks:
        _add(splib.fetch_top_tracks(token), "top_tracks")
    for pid in sources.playlist_ids or []:
        if not pid:
            continue
        _add(splib.fetch_playlist_tracks(pid, token), f"playlist:{pid}")
    return out


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


@app.post("/api/ingest/spotify", status_code=202)
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


@app.post("/api/ingest/single")
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


@app.post("/api/ingest/spotify-public", status_code=202)
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


_UNMATCHED_REPORT_CAP = 200
_PLATFORM_NAMES = {"spotify": "Spotify", "apple": "Apple Music", "youtube": "YouTube",
                   "deezer": "Deezer", "amazon": "Amazon Music"}


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


@app.post("/api/ingest/playlist-url", status_code=202)
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


@app.get("/api/ingest/status/{job_id}")
def ingest_status(job_id: str, sess: dict = Depends(require_user)):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if int(job.get("user_id") or 0) != int(sess["user_id"]):
            raise HTTPException(status_code=404, detail="job not found")
        return dict(job)


@app.delete("/api/ingest/status/{job_id}", status_code=204)
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


NEXT_DIR = Path(__file__).resolve().parent.parent / "frontend-next" / "dist"

# ---------------------------------------------------------------- routing ---
# The React app (frontend-next) is the only UI. The legacy vanilla app that
# used to live under /legacy has been removed now that React is at parity.
#
# ORDER MATTERS: the catch-all at the bottom must stay last, after every API
# route, or it swallows them.


@app.get("/next", include_in_schema=False)
@app.get("/next/{_path:path}", include_in_schema=False)
def serve_next_alias(_path: str = ""):
    """Historical prefix from the strangler phase, when React lived at /next
    to avoid colliding with the legacy app's root-level files. Kept as a
    redirect so old links and bookmarks still resolve."""
    return RedirectResponse(url="/", status_code=308)


# Cache policy for the built SPA. Vite names everything under /assets/ by
# content hash, so those bytes never change under one URL and may be cached
# for a year. Every other file (index.html, sw.js, manifest.json, icons) keeps
# its name across builds and must be revalidated, or a tab keeps the previous
# build's index.html and asks for chunks the new dist/ no longer has.
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
REVALIDATE_CACHE = "no-cache"


def _not_modified(request: Request, headers) -> bool:
    """True when the client's validators match the file. FileResponse sets
    ETag and Last-Modified but never answers a conditional request itself
    (Starlette 1.6; only StaticFiles does), so without this a no-cache
    revalidation re-downloads index.html every time instead of getting a 304."""
    inm = request.headers.get("if-none-match")
    if inm is not None:
        tags = {t.strip().removeprefix("W/") for t in inm.split(",")}
        return "*" in tags or headers["etag"] in tags
    ims = request.headers.get("if-modified-since")
    return ims is not None and ims == headers["last-modified"]


def _static_file(request: Request, file: Path, cache_control: str) -> Response:
    resp = FileResponse(str(file), headers={"Cache-Control": cache_control},
                        stat_result=os.stat(file))
    if _not_modified(request, resp.headers):
        return Response(status_code=304, headers={
            k: resp.headers[k] for k in ("etag", "last-modified", "cache-control")})
    return resp


@app.get("/{path:path}", include_in_schema=False)
def serve_react(request: Request, path: str = ""):
    """
    Serve the built React SPA.

    A real file (hashed asset, manifest.json, sw.js, icons/*) is served
    directly. A missing file is a 404 when the path is under /assets/ or has
    an extension: answering index.html there (200, text/html) is what turned a
    stale tab's lazy import of a chunk the last deploy deleted into "Failed to
    fetch dynamically imported module" plus a MIME error, which the client
    cannot tell from a real module (2026-10-21). Anything else is a client
    route and falls back to index.html so a hard refresh resolves.

    This is a catch-all and MUST remain the last route registered.
    """
    if not NEXT_DIR.is_dir():
        raise HTTPException(
            status_code=503,
            detail={"error": "frontend_not_built",
                    "hint": "cd frontend-next && npm install && npm run build"},
        )
    candidate = (NEXT_DIR / path).resolve()
    is_asset = path.startswith("assets/")
    # Containment check — never serve outside the dist directory.
    if path and candidate.is_file() and NEXT_DIR.resolve() in candidate.parents:
        return _static_file(request, candidate,
                            IMMUTABLE_CACHE if is_asset else REVALIDATE_CACHE)
    if is_asset or "." in path.rsplit("/", 1)[-1]:
        # no-store: a CDN must not keep this 404 for a name a newer build
        # may yet serve.
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    return _static_file(request, NEXT_DIR / "index.html", REVALIDATE_CACHE)
