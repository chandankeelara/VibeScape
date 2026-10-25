"""The DJ and /similar ranking helpers: the vibe path (_similar_vibe), the
request id parsing and caps, the per-user library mean the replay centres
on, the event-log replay glue, same-song filtering, and discovery mode's
library tags.

Moved out of app.py unchanged (refactor PR 5, 2026-10-25). Every function
opens its connection with db.get_conn() at call time (conftest.use_conn).
_dj_mean_cache is a module global that is mutated in place, never rebound;
tests clear it as `services.dj._dj_mean_cache`. The recency knobs stay in
services/recency.py, where tests patch them.
"""
import json
from datetime import datetime, timezone

from bootstrap import log

import numpy as np
from fastapi import HTTPException

# Through the module, never `from db import get_conn` (conftest.use_conn).
import db
import same_song as _same_song
from dj_replay import library_mean as _dj_mean_of
from dj_replay import parse_events as _dj_parse_events
from dj_replay import replay as _dj_replay
from track_rows import _row_to_dict, _search_rows, _track_select
from services.vectors import (FUSED_V1_AVOID, FUSED_V1_MODEL_VERSION, _embedding_column_for,
                              _load_mert_vecs_bulk, _resolve_anchor)
from services.recency import (_RecencyCandidate, _UTS_JOIN, _UTS_JOIN_BY_USER, _UTS_SELECT,
                              _dj_recency_enabled, _dj_recency_pool, _fetch_user_halflife,
                              _rerank_by_recency)


# Server-side caps on client-supplied id lists. The frontend already trims
# (DJ_MAX_SENT_IDS / DJ_MAX_EXCLUDES in frontend-next/src/features/queue/dj.js),
# but a request body is user input: without these a large list means one
# embedding row loaded per positive/negative id, and an arbitrarily long
# inlined NOT IN (...) clause for excludes.
_DJ_MAX_WEIGHTED_IDS = 50
_DJ_MAX_EXCLUDE_IDS = 200


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
