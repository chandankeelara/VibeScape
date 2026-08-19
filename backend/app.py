import glob
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db import ensure_db, get_conn

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ingest"))
try:
    import config as app_config
except Exception:
    app_config = None

import features as feat  # noqa: E402
import itunes_client  # noqa: E402
import scoring  # noqa: E402
import spotify_library as splib  # noqa: E402

log = logging.getLogger("vibescape")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

TRACK_COLUMNS = [
    "apple_id",
    "title",
    "artist",
    "album",
    "genre",
    "artwork_url",
    "preview_url",
    "track_view_url",
    "duration_ms",
    "vibe_score",
    "mood",
    "spotify_id",
    "classification_source",
    # extended derived / raw scalars exposed to frontend
    "activation",
    "valence",
    "activation_relative",
    "acousticness",
    "valence_mode",
    "tempo",
    "energy_mean",
]
TRACK_SELECT = ", ".join(TRACK_COLUMNS)

MOODS = list(scoring.MOODS)

AUDIO_DIR = PROJECT_ROOT / "data" / "audio"

app = FastAPI(title="VibeScape API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    ensure_db()
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def _row_to_dict(row):
    out = {}
    for col in TRACK_COLUMNS:
        try:
            out[col] = row[col]
        except (IndexError, KeyError):
            out[col] = None
    # frontend slider works against vibe_score, but callers may prefer
    # activation_relative once library z-scores exist. Expose both; when
    # activation_relative is populated, mirror it into vibe_score for
    # backward-compat filter queries.
    return out


@app.get("/api/health")
def health():
    conn = get_conn()
    try:
        count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    except Exception:
        count = 0
    finally:
        conn.close()
    return {"status": "ok", "track_count": count}


@app.get("/api/moods")
def moods():
    return {"moods": MOODS}


@app.get("/api/tracks")
def list_tracks(
    vibe_min: float = 0,
    vibe_max: float = 100,
    limit: int = 20,
    mood: Optional[str] = None,
    shuffle: bool = False,
):
    where = ["vibe_score BETWEEN ? AND ?"]
    params: list = [vibe_min, vibe_max]
    if mood:
        where.append("mood = ?")
        params.append(mood)

    order = "ORDER BY RANDOM()" if shuffle else "ORDER BY vibe_score"
    sql = f"SELECT {TRACK_SELECT} FROM tracks WHERE {' AND '.join(where)} {order} LIMIT ?"
    params.append(limit)

    conn = get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


@app.get("/api/tracks/random")
def random_track(
    vibe: float = Query(..., ge=0, le=100),
    tolerance: float = 12,
    exclude_ids: Optional[str] = None,
):
    exclude: list = []
    if exclude_ids:
        for part in exclude_ids.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                exclude.append(int(part))
            except ValueError:
                continue

    conn = get_conn()
    try:
        for attempt in range(2):
            tol = tolerance + (10 if attempt else 0)
            lo, hi = vibe - tol, vibe + tol
            sql = f"SELECT {TRACK_SELECT} FROM tracks WHERE vibe_score BETWEEN ? AND ?"
            params: list = [lo, hi]
            if exclude:
                placeholders = ",".join("?" * len(exclude))
                sql += f" AND apple_id NOT IN ({placeholders})"
                params.extend(exclude)
            sql += " ORDER BY RANDOM() LIMIT 1"
            try:
                row = conn.execute(sql, params).fetchone()
            except Exception:
                row = None
            if row:
                return _row_to_dict(row)
    finally:
        conn.close()

    raise HTTPException(status_code=404, detail={"error": "no tracks in vibe range"})


@app.get("/api/spotify/config")
def spotify_config():
    client_id = getattr(app_config, "SPOTIFY_CLIENT_ID", "") if app_config else ""
    redirect_uri = getattr(app_config, "SPOTIFY_REDIRECT_URI", "") if app_config else ""
    return {"client_id": client_id or "", "redirect_uri": redirect_uri or ""}


@app.get("/api/track/{apple_id}/spotify")
def get_track_spotify(apple_id: int):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT spotify_id FROM tracks WHERE apple_id = ?", (apple_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["spotify_id"]:
        raise HTTPException(status_code=404, detail="no spotify id for track")
    return {"spotify_id": row["spotify_id"], "uri": f"spotify:track:{row['spotify_id']}"}


_CALLBACK_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>VibeScape - Spotify</title>
<style>body{background:#08080c;color:#f2f2f5;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}</style>
</head><body>
<div>
  <p id="msg">Signing you in…</p>
</div>
<noscript>
  <meta http-equiv="refresh" content="0;url=/">
</noscript>
<script>
(function(){
  try {
    var params = new URLSearchParams(window.location.search);
    var code = params.get('code');
    var error = params.get('error');
    var state = params.get('state');
    var payload = {
      code: code || null,
      error: error || null,
      state: state || null,
      ts: Date.now()
    };
    try { localStorage.setItem('spotify_pending_auth', JSON.stringify(payload)); } catch(e){}
    var msgEl = document.getElementById('msg');
    var isPopup = !!window.opener || window.name === 'vibescape-oauth-popup';
    if (isPopup) {
      if (msgEl) msgEl.textContent = 'Signed in. Closing this window…';
      try { window.opener && window.opener.focus(); } catch(e){}
      setTimeout(function(){ try { window.close(); } catch(e){} }, 80);
    } else {
      if (msgEl) msgEl.textContent = 'Signed in. Redirecting…';
      var out = new URLSearchParams();
      if (code) out.set('spotify_code', code);
      if (error) out.set('spotify_error', error);
      if (state) out.set('spotify_state', state);
      var qs = out.toString();
      window.location.replace('/' + (qs ? ('?' + qs) : ''));
    }
  } catch(e) {
    var el = document.getElementById('msg');
    if (el) el.textContent = 'Auth error: ' + e.message;
  }
})();
</script>
</body></html>
"""


@app.get("/callback")
def spotify_callback():
    return HTMLResponse(_CALLBACK_HTML)


_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _resolve_audio_path(audio_path: str) -> Optional[Path]:
    if not audio_path:
        return None
    p = Path(audio_path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    try:
        p = p.resolve()
    except OSError:
        return None
    try:
        p.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return None
    return p


def _stream_audio_row(row, request: Request):
    if not row or not row["audio_path"]:
        raise HTTPException(status_code=404, detail="audio not found")

    audio_file = _resolve_audio_path(row["audio_path"])
    if not audio_file or not audio_file.exists() or not audio_file.is_file():
        raise HTTPException(status_code=404, detail="audio file missing on disk")

    file_size = audio_file.stat().st_size
    range_header = request.headers.get("range") or request.headers.get("Range")

    ext = audio_file.suffix.lower()
    media_type = "audio/mpeg" if ext == ".mp3" else "audio/mp4"

    common_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=3600",
        "Content-Type": media_type,
    }

    if range_header:
        m = _RANGE_RE.match(range_header.strip())
        if not m:
            raise HTTPException(status_code=416, detail="invalid range")
        start_s, end_s = m.group(1), m.group(2)
        if start_s == "" and end_s == "":
            raise HTTPException(status_code=416, detail="invalid range")
        if start_s == "":
            suffix = int(end_s)
            if suffix <= 0:
                raise HTTPException(status_code=416, detail="invalid range")
            start = max(0, file_size - suffix)
            end = file_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1

        if start >= file_size or end >= file_size or start > end:
            headers = {"Content-Range": f"bytes */{file_size}"}
            return Response(status_code=416, headers=headers)

        length = end - start + 1

        def _iter_range(path: Path, offset: int, remaining: int, chunk: int = 64 * 1024):
            with open(path, "rb") as f:
                f.seek(offset)
                while remaining > 0:
                    data = f.read(min(chunk, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            **common_headers,
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(length),
        }
        return StreamingResponse(
            _iter_range(audio_file, start, length),
            status_code=206,
            headers=headers,
            media_type=media_type,
        )

    def _iter_full(path: Path, chunk: int = 64 * 1024):
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk)
                if not data:
                    break
                yield data

    headers = {
        **common_headers,
        "Content-Length": str(file_size),
    }
    return StreamingResponse(
        _iter_full(audio_file),
        status_code=200,
        headers=headers,
        media_type=media_type,
    )


@app.get("/api/stream/spotify/{spotify_id}")
def stream_track_by_spotify(spotify_id: str, request: Request):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT audio_path FROM tracks WHERE spotify_id = ?", (spotify_id,)
        ).fetchone()
    finally:
        conn.close()
    return _stream_audio_row(row, request)


@app.get("/api/stream/{track_key}")
def stream_track(track_key: str, request: Request):
    conn = get_conn()
    try:
        row = None
        try:
            apple_id_int = int(track_key)
            row = conn.execute(
                "SELECT audio_path FROM tracks WHERE apple_id = ?", (apple_id_int,)
            ).fetchone()
        except ValueError:
            pass
        if not row:
            row = conn.execute(
                "SELECT audio_path FROM tracks WHERE spotify_id = ?", (track_key,)
            ).fetchone()
    finally:
        conn.close()
    return _stream_audio_row(row, request)


# ---------------- Feature reconstitution + scoring helpers ----------------


_FEATURE_SCALARS = [
    "tempo",
    "tempo_stability",
    "onset_rate",
    "energy_mean",
    "energy_std",
    "brightness",
    "bandwidth",
    "rolloff",
    "spectral_contrast",
    "flatness",
    "zcr",
    "timbre_variability",
    "valence_mode",
    "tonnetz_std",
    "acousticness",
]


def _row_features(row) -> dict:
    """Rebuild a feature-dict shape from a DB row (for scoring / API responses)."""
    f: dict = {}
    for k in _FEATURE_SCALARS:
        try:
            f[k] = row[k]
        except (IndexError, KeyError):
            f[k] = None
    # Legacy alias support
    try:
        if f.get("energy_mean") is None:
            f["energy_mean"] = row["energy"]
    except (IndexError, KeyError):
        pass
    try:
        mfcc_json = row["mfcc_json"]
        f["mfcc_mean"] = json.loads(mfcc_json) if mfcc_json else []
    except (IndexError, KeyError, ValueError, TypeError):
        f["mfcc_mean"] = []
    try:
        chroma_json = row["chroma_mean_json"]
        f["chroma_mean"] = json.loads(chroma_json) if chroma_json else []
    except (IndexError, KeyError, ValueError, TypeError):
        f["chroma_mean"] = []
    return f


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _recompute_axes_and_zscores(conn) -> dict:
    """
    For every track that has stored features, recompute activation/valence
    from scoring.compute_axes(), then compute library-wide z-scored
    activation_relative and update the row. Returns summary stats.

    Runs synchronously against `conn`. Cheap: pure DB + math, no audio.
    """
    rows = conn.execute("SELECT id, tempo, energy, energy_mean, energy_std, brightness, "
                        "tempo_stability, onset_rate, bandwidth, rolloff, "
                        "spectral_contrast, flatness, zcr, timbre_variability, "
                        "valence_mode, tonnetz_std, acousticness, mfcc_json, "
                        "chroma_mean_json FROM tracks").fetchall()

    activations: list[float] = []
    per_row: list[tuple[int, float, float]] = []
    for row in rows:
        f = _row_features(row)
        # If no scalars at all, skip (leaves existing values intact).
        if all((f.get(k) is None) for k in _FEATURE_SCALARS):
            continue
        axes = scoring.compute_axes(f)
        activation = axes["activation"]
        valence = axes["valence"]
        activations.append(activation)
        per_row.append((row["id"], activation, valence))

    if not per_row:
        return {"updated": 0, "activation_stats": None, "mood_distribution": {}}

    import statistics as _stats
    mean = float(_stats.fmean(activations))
    std = float(_stats.pstdev(activations)) if len(activations) > 1 else 0.0
    mn = float(min(activations))
    mx = float(max(activations))

    mood_counts: dict[str, int] = {}
    for row_id, activation, valence in per_row:
        if std > 1e-9:
            rel = 50.0 + ((activation - mean) / std) * 15.0
        else:
            rel = 50.0
        rel = _clamp(rel, 0.0, 100.0)
        mood = scoring.mood_label(activation, valence)
        mood_counts[mood] = mood_counts.get(mood, 0) + 1
        conn.execute(
            "UPDATE tracks SET activation = ?, valence = ?, activation_relative = ?, "
            "vibe_score = ?, mood = ? WHERE id = ?",
            (activation, valence, rel, rel, mood, row_id),
        )
    conn.commit()

    return {
        "updated": len(per_row),
        "activation_stats": {
            "mean": mean,
            "std": std,
            "min": mn,
            "max": mx,
        },
        "mood_distribution": mood_counts,
    }


@app.post("/api/recompute-scores")
def api_recompute_scores():
    """
    Re-run scoring.compute_axes on every track using persisted features,
    then z-score-normalize activation into activation_relative and mirror
    into vibe_score. Cheap: no audio re-analysis.
    """
    conn = get_conn()
    try:
        summary = _recompute_axes_and_zscores(conn)
    finally:
        conn.close()
    return summary


@app.get("/api/tracks/{track_key}/features")
def get_track_features(track_key: str):
    """
    Return the full stored feature blob plus derived axes for a track,
    keyed by spotify_id (string) or apple_id (numeric string).
    """
    conn = get_conn()
    try:
        row = None
        try:
            apple_id_int = int(track_key)
            row = conn.execute("SELECT * FROM tracks WHERE apple_id = ?", (apple_id_int,)).fetchone()
        except ValueError:
            pass
        if not row:
            row = conn.execute("SELECT * FROM tracks WHERE spotify_id = ?", (track_key,)).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="track not found")

    def _val(col):
        try:
            return row[col]
        except (IndexError, KeyError):
            return None

    mfcc_mean = []
    chroma_mean = []
    try:
        if _val("mfcc_json"):
            mfcc_mean = json.loads(row["mfcc_json"])
    except (ValueError, TypeError):
        mfcc_mean = []
    try:
        if _val("chroma_mean_json"):
            chroma_mean = json.loads(row["chroma_mean_json"])
    except (ValueError, TypeError):
        chroma_mean = []

    return {
        "apple_id": _val("apple_id"),
        "spotify_id": _val("spotify_id"),
        "title": _val("title"),
        "artist": _val("artist"),
        "features": {
            "tempo": _val("tempo"),
            "tempo_stability": _val("tempo_stability"),
            "onset_rate": _val("onset_rate"),
            "energy_mean": _val("energy_mean") if _val("energy_mean") is not None else _val("energy"),
            "energy_std": _val("energy_std"),
            "brightness": _val("brightness"),
            "bandwidth": _val("bandwidth"),
            "rolloff": _val("rolloff"),
            "spectral_contrast": _val("spectral_contrast"),
            "flatness": _val("flatness"),
            "zcr": _val("zcr"),
            "timbre_variability": _val("timbre_variability"),
            "valence_mode": _val("valence_mode"),
            "tonnetz_std": _val("tonnetz_std"),
            "acousticness": _val("acousticness"),
            "mfcc_mean": mfcc_mean,
            "chroma_mean": chroma_mean,
        },
        "axes": {
            "activation": _val("activation"),
            "valence": _val("valence"),
            "activation_relative": _val("activation_relative"),
        },
        "mood": _val("mood"),
        "classification_source": _val("classification_source"),
    }


# ---------------- Spotify library ingest ----------------

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _bearer_token(auth_header: Optional[str]) -> str:
    if not auth_header:
        raise HTTPException(status_code=401, detail={"error": "missing_authorization"})
    parts = auth_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail={"error": "invalid_authorization"})
    return parts[1].strip()


@app.get("/api/spotify/library")
def spotify_library(authorization: Optional[str] = Header(None)):
    token = _bearer_token(authorization)
    try:
        liked = splib.get_liked_count(token)
        top = splib.get_top_tracks_count(token)
        playlists_raw = splib.get_playlists(token, max_items=200)
    except splib.SpotifyAuthError:
        return JSONResponse(status_code=401, content={"error": "spotify_token_expired"})
    except splib.SpotifyAPIError as e:
        raise HTTPException(status_code=500, detail=str(e))

    playlists = []
    for p in playlists_raw:
        if not p:
            continue
        owner = (p.get("owner") or {}).get("display_name") or (p.get("owner") or {}).get("id") or ""
        tracks_info = p.get("tracks") or {}
        playlists.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "track_count": int(tracks_info.get("total") or 0),
            "owner": owner,
        })
    return {
        "liked_count": liked,
        "top_tracks_count": top,
        "playlists": playlists,
    }


@app.post("/api/ingest/clear")
def ingest_clear():
    conn = get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        conn.execute("DELETE FROM tracks")
        conn.commit()
    finally:
        conn.close()

    removed_files = 0
    if AUDIO_DIR.exists():
        for f in AUDIO_DIR.glob("*"):
            if f.is_file():
                try:
                    f.unlink()
                    removed_files += 1
                except OSError as e:
                    log.warning("failed to unlink %s: %s", f, e)
    log.info("ingest/clear removed %d tracks, %d audio files", n, removed_files)
    return {"cleared": int(n), "audio_files_removed": removed_files}


class IngestSources(BaseModel):
    liked: bool = False
    top_tracks: bool = False
    playlist_ids: list[str] = []


class IngestRequest(BaseModel):
    access_token: str
    sources: IngestSources


def _itunes_lookup_by_isrc(isrc: str) -> Optional[dict]:
    try:
        r = requests.get("https://itunes.apple.com/lookup", params={"isrc": isrc}, timeout=15)
        r.raise_for_status()
        results = r.json().get("results") or []
        return results[0] if results else None
    except requests.RequestException as e:
        log.warning("itunes ISRC lookup failed for %s: %s", isrc, e)
        return None


def _itunes_search_track(title: str, artist: str) -> Optional[dict]:
    if not title or not artist:
        return None
    term = f"{title} {artist}".strip()
    try:
        results = itunes_client.search(term, limit=5)
    except Exception as e:
        log.warning("itunes search failed for %r: %s", term, e)
        return None
    if not results:
        return None
    title_l = title.lower()
    artist_l = artist.lower()
    for r in results:
        if not isinstance(r, dict) or not r.get("previewUrl"):
            continue
        r_title = (r.get("trackName") or "").lower()
        r_artist = (r.get("artistName") or "").lower()
        if r_title == title_l and r_artist == artist_l:
            return r
    for r in results:
        if not isinstance(r, dict) or not r.get("previewUrl"):
            continue
        r_title = (r.get("trackName") or "").lower()
        r_artist = (r.get("artistName") or "").lower()
        if title_l in r_title and artist_l in r_artist:
            return r
    for r in results:
        if isinstance(r, dict) and r.get("previewUrl"):
            return r
    return None


def _download(url: str, suffix: str) -> str:
    r = requests.get(url, timeout=30, stream=True)
    r.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=suffix)
    with open(fd, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return path


def _update_job(job_id: str, **fields):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job.update(fields)


def _bump(job_id: str, key: str, amount: int = 1):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job[key] = int(job.get(key, 0)) + amount


def _process_track(conn, track: dict, job_id: str) -> str:
    spotify_id = track.get("id")
    if not spotify_id:
        return "skip:no_id"

    name = track.get("name") or "?"
    artists = track.get("artists") or []
    artist_name = (artists[0].get("name") if artists and isinstance(artists[0], dict) else "?") or "?"
    album_obj = track.get("album") or {}
    album_name = album_obj.get("name")
    images = album_obj.get("images") or []
    artwork_url = images[0].get("url") if images and isinstance(images[0], dict) else None
    duration_ms = track.get("duration_ms")
    ext_ids = track.get("external_ids") or {}
    isrc = ext_ids.get("isrc")
    preview_url = track.get("preview_url")

    _update_job(job_id, current_track=f"{name} - {artist_name}")
    log.info("[ingest] track=%r artist=%r spotify_preview=%s isrc=%s",
             name, artist_name, bool(preview_url), isrc or "-")

    existing = conn.execute("SELECT id FROM tracks WHERE spotify_id = ?", (spotify_id,)).fetchone()
    if existing:
        return "skip:already_ingested"

    audio_url = None
    audio_ext = ".mp3"
    apple_id = None
    itunes_result = None
    source = None
    classification_source = "none"

    if preview_url:
        audio_url = preview_url
        audio_ext = ".mp3"
        source = "spotify"
        classification_source = "spotify_preview"
    else:
        if isrc:
            itunes_result = _itunes_lookup_by_isrc(isrc)
            log.info("[ingest]   itunes isrc-lookup isrc=%s -> hit=%s",
                     isrc, bool(itunes_result and itunes_result.get("previewUrl")))
            if itunes_result and itunes_result.get("previewUrl"):
                classification_source = "itunes_isrc"
        if not (itunes_result and itunes_result.get("previewUrl")):
            itunes_result = _itunes_search_track(name, artist_name)
            log.info("[ingest]   itunes term-search %r/%r -> hit=%s",
                     name, artist_name,
                     bool(itunes_result and itunes_result.get("previewUrl")))
            if itunes_result and itunes_result.get("previewUrl"):
                classification_source = "itunes_term_search"
        if itunes_result and itunes_result.get("previewUrl"):
            audio_url = itunes_result["previewUrl"]
            audio_ext = ".m4a"
            apple_id = itunes_result.get("trackId")
            source = "itunes"

    if not audio_url:
        log.info("[ingest]   -> no_preview (spotify+itunes both failed)")
        return "skip:no_preview"

    tmp_path = None
    try:
        tmp_path = _download(audio_url, audio_ext)
        f = feat.extract(tmp_path)
        axes = scoring.compute_axes(f)
        activation = axes["activation"]
        valence = axes["valence"]
        mood = scoring.mood_label(activation, valence)

        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        target_name = f"{spotify_id}{audio_ext}"
        target = AUDIO_DIR / target_name
        try:
            shutil.copyfile(tmp_path, target)
            audio_rel = f"data/audio/{target_name}"
        except OSError as e:
            log.warning("failed to save audio for %s: %s", spotify_id, e)
            audio_rel = None

        merged_preview_url = preview_url or (itunes_result.get("previewUrl") if itunes_result else None)
        merged_genre = (itunes_result or {}).get("primaryGenreName")
        merged_track_view_url = (itunes_result or {}).get("trackViewUrl")

        track_data = {
            "apple_id": apple_id,
            "spotify_id": spotify_id,
            "isrc": isrc,
            "title": name,
            "artist": artist_name,
            "album": album_name or (itunes_result or {}).get("collectionName"),
            "genre": merged_genre,
            "artwork_url": artwork_url or (itunes_result or {}).get("artworkUrl100"),
            "preview_url": merged_preview_url,
            "track_view_url": merged_track_view_url,
            "duration_ms": duration_ms or (itunes_result or {}).get("trackTimeMillis"),
            # legacy scalars
            "tempo": f.get("tempo"),
            "energy": f.get("energy_mean"),
            "brightness": f.get("brightness"),
            "zcr": f.get("zcr"),
            "mfcc_json": json.dumps(f.get("mfcc_mean") or f.get("mfcc") or []),
            # extended scalars
            "tempo_stability": f.get("tempo_stability"),
            "onset_rate": f.get("onset_rate"),
            "energy_mean": f.get("energy_mean"),
            "energy_std": f.get("energy_std"),
            "bandwidth": f.get("bandwidth"),
            "rolloff": f.get("rolloff"),
            "spectral_contrast": f.get("spectral_contrast"),
            "flatness": f.get("flatness"),
            "timbre_variability": f.get("timbre_variability"),
            "valence_mode": f.get("valence_mode"),
            "tonnetz_std": f.get("tonnetz_std"),
            "acousticness": f.get("acousticness"),
            "chroma_mean_json": json.dumps(f.get("chroma_mean") or []),
            # derived axes
            "activation": activation,
            "valence": valence,
            "vibe_score": activation,  # backward-compat; z-score pass replaces with activation_relative
            "mood": mood,
            "audio_path": audio_rel,
            "classification_source": classification_source,
        }
        _upsert(conn, track_data)
        return f"ok:{source}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _upsert(conn, td: dict) -> None:
    cols = [
        "apple_id", "spotify_id", "isrc", "title", "artist", "album", "genre",
        "artwork_url", "preview_url", "track_view_url", "duration_ms",
        "tempo", "energy", "brightness", "zcr", "mfcc_json",
        "tempo_stability", "onset_rate", "energy_mean", "energy_std",
        "bandwidth", "rolloff", "spectral_contrast", "flatness",
        "timbre_variability", "valence_mode", "tonnetz_std", "acousticness",
        "chroma_mean_json",
        "activation", "valence", "activation_relative",
        "vibe_score", "mood", "audio_path", "classification_source",
    ]
    values = [td.get(c) for c in cols]
    existing = None
    if td.get("spotify_id"):
        row = conn.execute("SELECT id FROM tracks WHERE spotify_id = ?", (td["spotify_id"],)).fetchone()
        if row:
            existing = row[0]
    if existing is None and td.get("apple_id"):
        row = conn.execute("SELECT id FROM tracks WHERE apple_id = ?", (td["apple_id"],)).fetchone()
        if row:
            existing = row[0]

    if existing is None:
        placeholders = ", ".join(["?"] * len(cols))
        conn.execute(f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({placeholders})", values)
    else:
        set_clause = ", ".join([f"{c} = ?" for c in cols])
        conn.execute(f"UPDATE tracks SET {set_clause} WHERE id = ?", values + [existing])
    conn.commit()


def _collect_tracks(token: str, sources: IngestSources) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []

    def _add(items: list[dict]):
        for t in items:
            tid = t.get("id") if isinstance(t, dict) else None
            if not tid or tid in seen:
                continue
            seen.add(tid)
            out.append(t)

    if sources.liked:
        _add(splib.fetch_liked(token))
    if sources.top_tracks:
        _add(splib.fetch_top_tracks(token))
    for pid in sources.playlist_ids or []:
        if not pid:
            continue
        _add(splib.fetch_playlist_tracks(pid, token))
    return out


def _run_ingest_job(job_id: str, token: str, sources: IngestSources):
    try:
        _update_job(job_id, status="running", current_track="collecting library…")
        try:
            tracks = _collect_tracks(token, sources)
        except splib.SpotifyAuthError:
            _update_job(job_id, status="error", error_message="spotify_token_expired")
            return
        except splib.SpotifyAPIError as e:
            _update_job(job_id, status="error", error_message=f"spotify_api_error: {e}")
            return

        _update_job(job_id, total=len(tracks))

        conn = get_conn()
        try:
            for track in tracks:
                try:
                    result = _process_track(conn, track, job_id)
                    if result == "ok:spotify":
                        _bump(job_id, "matched_spotify", 1)
                    elif result == "ok:itunes":
                        _bump(job_id, "preview_only", 1)
                    elif result == "skip:no_preview":
                        _bump(job_id, "no_preview", 1)
                    else:
                        _bump(job_id, "skipped", 1)
                except splib.SpotifyAuthError:
                    _update_job(job_id, status="error", error_message="spotify_token_expired")
                    return
                except Exception as e:
                    log.exception("track ingest failed: %s", e)
                    _bump(job_id, "skipped", 1)
                _bump(job_id, "processed", 1)
        finally:
            conn.close()

        # library-wide z-score recompute so the vibe slider reflects the
        # actual distribution of the (now larger) library.
        try:
            conn2 = get_conn()
            try:
                _recompute_axes_and_zscores(conn2)
            finally:
                conn2.close()
        except Exception as e:
            log.exception("post-ingest recompute failed: %s", e)

        _update_job(job_id, status="complete", current_track=None)
    except Exception as e:
        log.exception("ingest job crashed: %s", e)
        _update_job(job_id, status="error", error_message=f"{e.__class__.__name__}: {e}")


@app.post("/api/ingest/spotify", status_code=202)
def ingest_spotify(req: IngestRequest):
    if not req.access_token:
        raise HTTPException(status_code=422, detail="access_token required")
    if not (req.sources.liked or req.sources.top_tracks or req.sources.playlist_ids):
        raise HTTPException(status_code=422, detail="no sources selected")
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "pending",
            "current_track": None,
            "total": 0,
            "processed": 0,
            "matched_spotify": 0,
            "preview_only": 0,
            "no_preview": 0,
            "skipped": 0,
            "error_message": None,
        }
    t = threading.Thread(
        target=_run_ingest_job,
        args=(job_id, req.access_token, req.sources),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id}


@app.get("/api/ingest/status/{job_id}")
def ingest_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return dict(job)


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
