import glob
import os
import shutil
import socket
import tempfile
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# First: loads .env, imports backend/db.py, then puts the repo root and
# ingest/ on sys.path, in that order (see its docstring).
import bootstrap
from bootstrap import AUDIO_DIR, log

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel

# Through the module, never `from db import get_conn`: db.get_conn is the one
# seam tests patch (conftest.use_conn), and a copied name would not see it.
import db
from deps import require_user  # auth dependency
import same_song as _same_song
from track_rows import _row_to_dict, _track_select
from services.vectors import (FUSED_COLUMN, _DJ_VARIANT_DEFAULT, _embedding_column_for,
                              _l2_normalize, _load_mert_vec, _load_mert_vecs_bulk,
                              _resolve_anchor, _variant_spec)
from services.recency import (_RecencyCandidate, _UTS_JOIN, _UTS_JOIN_BY_USER, _UTS_SELECT,
                              _dj_recency_enabled, _dj_recency_pool, _fetch_recency_stats,
                              _fetch_user_halflife, _recency_penalty, _rerank_by_recency)
from services.dj import (_DJ_MAX_EXCLUDE_IDS, _DJ_MAX_WEIGHTED_IDS, _discover_finish,
                         _dj_drop_repeats, _dj_events_query, _parse_id_weight_list,
                         _resolve_ids_to_track_ids, _similar_vibe)
from routers import admin as admin_routes
from routers import auth as auth_routes
from routers import events as events_routes
from routers import ingest as ingest_routes
from routers import me as me_routes
from routers import meta as meta_routes
from routers import register as register_routes
from routers import spotify as spotify_routes
from routers import stream as stream_routes
from routers import tracks as tracks_routes
from routers import youtube as youtube_routes

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
register_routes(app, ingest_routes.router)


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
