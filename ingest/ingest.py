import argparse
import io
import json
import os
import shutil
import sys
import statistics
import time
import traceback

# Force stdout/stderr to UTF-8 so non-ASCII track titles (Kannada, Bengali,
# Chinese, etc.) don't crash `print` when running under Windows' cp1252
# console/file default. errors='replace' guards against any residual weirdness.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
else:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests

import itunes_client
import features as feat
import scoring
import db
import spotify_matcher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import config
except Exception:
    config = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(ROOT, "data", "audio")


def _ensure_audio_dir() -> None:
    os.makedirs(AUDIO_DIR, exist_ok=True)


def _audio_target(apple_id: int) -> str:
    return os.path.join(AUDIO_DIR, f"{apple_id}.m4a")


def _relative_audio_path(apple_id: int) -> str:
    return f"data/audio/{apple_id}.m4a"


def already_ingested(conn, apple_id: int) -> bool:
    cur = conn.execute("SELECT 1 FROM tracks WHERE apple_id = ?", (apple_id,))
    return cur.fetchone() is not None


def _download_to(url: str, dest_path: str) -> None:
    r = requests.get(url, timeout=30, stream=True)
    r.raise_for_status()
    tmp = dest_path + ".part"
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    os.replace(tmp, dest_path)


def run_backfill() -> None:
    _ensure_audio_dir()
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT apple_id, title, artist, preview_url, audio_path FROM tracks ORDER BY apple_id"
        ).fetchall()
        total = len(rows)
        ok = 0
        already = 0
        failed = 0
        for i, row in enumerate(rows, start=1):
            apple_id = row["apple_id"]
            title = row["title"]
            artist = row["artist"]
            preview_url = row["preview_url"]
            target = _audio_target(apple_id)
            rel = _relative_audio_path(apple_id)

            if os.path.exists(target) and os.path.getsize(target) > 0:
                if row["audio_path"] != rel:
                    db.update_audio_path(conn, apple_id, rel)
                already += 1
                print(f"[backfill {i}/{total}] {title} - {artist} (already present)")
                continue

            if not preview_url:
                failed += 1
                print(f"[backfill {i}/{total}] {title} - {artist} (no preview_url, skipped)")
                continue

            try:
                print(f"[backfill {i}/{total}] {title} - {artist}")
                _download_to(preview_url, target)
                db.update_audio_path(conn, apple_id, rel)
                ok += 1
            except Exception as e:
                failed += 1
                print(f"  [err] {e.__class__.__name__}: {e}")
                if os.path.exists(target + ".part"):
                    try:
                        os.remove(target + ".part")
                    except OSError:
                        pass
        print(f"\n=== backfill done: downloaded={ok} already={already} failed={failed} total={total} ===")
    finally:
        conn.close()


def run_match_spotify() -> None:
    client_id = getattr(config, "SPOTIFY_CLIENT_ID", "") if config else ""
    client_secret = getattr(config, "SPOTIFY_CLIENT_SECRET", "") if config else ""

    if not client_id or not client_secret:
        print("populate SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in config.py before running --match-spotify")
        sys.exit(1)

    token = os.environ.get("SPOTIFY_TOKEN")
    if not token:
        try:
            token = spotify_matcher.get_client_credentials_token(client_id, client_secret)
        except Exception as e:
            print(f"failed to obtain client credentials token: {e.__class__.__name__}: {e}")
            sys.exit(1)

    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT apple_id, isrc, title, artist, spotify_id FROM tracks ORDER BY apple_id"
        ).fetchall()
        total = len(rows)
        matched = 0
        already = 0
        missed = 0
        for i, row in enumerate(rows, start=1):
            apple_id = row["apple_id"]
            title = row["title"]
            artist = row["artist"]
            isrc = row["isrc"]
            existing = row["spotify_id"]

            if existing:
                already += 1
                print(f"[{i}/{total}] {title} - {artist} (already: spotify:track:{existing})")
                continue

            sid = None
            try:
                if isrc:
                    sid = spotify_matcher.match_by_isrc(isrc, token)
                if not sid:
                    sid = spotify_matcher.match_by_title_artist(title, artist, token)
            except Exception as e:
                print(f"[{i}/{total}] {title} - {artist} error: {e.__class__.__name__}: {e}")

            if sid:
                db.update_spotify_id(conn, apple_id, sid)
                matched += 1
                print(f"[{i}/{total}] {title} - {artist} -> spotify:track:{sid}")
            else:
                missed += 1
                print(f"[{i}/{total}] {title} - {artist} (no match)")

        print(f"\n=== match-spotify done: matched={matched} already={already} missed={missed} total={total} ===")
    finally:
        conn.close()


def _resolve_local_audio(rel_or_abs: str) -> str | None:
    if not rel_or_abs:
        return None
    if os.path.isabs(rel_or_abs) and os.path.exists(rel_or_abs):
        return rel_or_abs
    candidate = os.path.join(ROOT, rel_or_abs)
    if os.path.exists(candidate):
        return candidate
    return None


_V2_REQUIRED = [
    "tempo_stability", "onset_rate", "energy_std", "bandwidth", "rolloff",
    "spectral_contrast", "flatness", "timbre_variability", "valence_mode",
    "tonnetz_std", "acousticness",
]

_PROGRESS_PATH = os.path.join(ROOT, "data", "recompute_progress.json")


def _write_progress(payload: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_PROGRESS_PATH), exist_ok=True)
        tmp = _PROGRESS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, _PROGRESS_PATH)
    except OSError:
        pass


def _safe_print(msg: str) -> None:
    """
    Print with a hard guarantee it won't blow up on encoding: even if
    the sys.stdout reconfigure at module load failed and we're stuck on
    cp1252, transliterate the message to ASCII with '?' replacements as
    a last-resort so the run never crashes on a non-ASCII title.
    """
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        safe = msg.encode("ascii", errors="replace").decode("ascii")
        print(safe, flush=True)


def run_recompute_features(limit: int | None = None, force: bool = False) -> None:
    """
    Re-run librosa feature extraction on every track that has a local audio
    file on disk, using the extended `extract_full` set. Update DB. Then
    run library-wide z-score normalization so activation_relative /
    vibe_score / mood are all consistent.

    Idempotent: skips tracks whose v2 columns are already fully populated
    (unless force=True). Writes progress to data/recompute_progress.json
    after every track so external pollers can watch state without needing
    to parse stdout.
    """
    conn = db.get_conn()
    try:
        select_cols = "id, spotify_id, apple_id, title, artist, audio_path, " + ", ".join(_V2_REQUIRED)
        rows = conn.execute(
            f"SELECT {select_cols} FROM tracks "
            "WHERE audio_path IS NOT NULL AND audio_path != '' ORDER BY id"
        ).fetchall()
        if limit:
            rows = rows[:limit]
        total = len(rows)

        # Idempotent skip: tracks with ALL v2 columns populated already are done.
        pending = []
        already = 0
        for row in rows:
            if not force and all(row[c] is not None for c in _V2_REQUIRED):
                already += 1
            else:
                pending.append(row)

        _safe_print(f"[recompute] total={total} already_complete={already} pending={len(pending)}")
        _write_progress({
            "status": "running", "total": total, "already_complete": already,
            "pending": len(pending), "processed_this_run": 0,
            "ok": 0, "failed": 0, "skipped": 0, "last_track": None,
            "started_ts": time.time(),
        })

        ok = 0
        skipped = 0
        failed = 0
        failed_tracks: list[str] = []
        t0 = time.time()
        for i, row in enumerate(pending, start=1):
            audio_path = _resolve_local_audio(row["audio_path"])
            title = row["title"]
            artist = row["artist"]
            if not audio_path:
                skipped += 1
                _safe_print(f"[{i}/{len(pending)}] {title} - {artist} (audio missing: {row['audio_path']})")
                _write_progress({
                    "status": "running", "total": total,
                    "already_complete": already, "pending": len(pending),
                    "processed_this_run": i, "ok": ok, "failed": failed,
                    "skipped": skipped, "last_track": f"{title} - {artist}",
                })
                continue
            try:
                t1 = time.time()
                f = feat.extract(audio_path)
                axes = scoring.compute_axes(f)
                mood = scoring.mood_label(axes["activation"], axes["valence"])
                conn.execute(
                    """
                    UPDATE tracks SET
                      tempo = ?, tempo_stability = ?, onset_rate = ?,
                      energy_mean = ?, energy = ?, energy_std = ?,
                      brightness = ?, bandwidth = ?, rolloff = ?,
                      spectral_contrast = ?, flatness = ?, zcr = ?,
                      timbre_variability = ?, valence_mode = ?, tonnetz_std = ?,
                      acousticness = ?,
                      mfcc_json = ?, chroma_mean_json = ?,
                      activation = ?, valence = ?, vibe_score = ?, mood = ?
                    WHERE id = ?
                    """,
                    (
                        f.get("tempo"), f.get("tempo_stability"), f.get("onset_rate"),
                        f.get("energy_mean"), f.get("energy_mean"), f.get("energy_std"),
                        f.get("brightness"), f.get("bandwidth"), f.get("rolloff"),
                        f.get("spectral_contrast"), f.get("flatness"), f.get("zcr"),
                        f.get("timbre_variability"), f.get("valence_mode"), f.get("tonnetz_std"),
                        f.get("acousticness"),
                        json.dumps(f.get("mfcc_mean") or []),
                        json.dumps(f.get("chroma_mean") or []),
                        axes["activation"], axes["valence"], axes["activation"], mood,
                        row["id"],
                    ),
                )
                conn.commit()
                ok += 1
                dt = time.time() - t1
                total_dt = time.time() - t0
                eta = (total_dt / i) * (len(pending) - i) if i else 0
                _safe_print(f"[{i}/{len(pending)}] {title} - {artist}  act={axes['activation']:.1f} val={axes['valence']:.1f} mood={mood}  ({dt:.1f}s, eta {eta/60:.1f}m)")
            except Exception as e:
                failed += 1
                failed_tracks.append(f"{title} - {artist} [{row['audio_path']}] {e.__class__.__name__}: {e}")
                try:
                    traceback.print_exc()
                except UnicodeEncodeError:
                    pass
                _safe_print(f"[{i}/{len(pending)}] {title} - {artist} FAILED: {e.__class__.__name__}: {e}")
            _write_progress({
                "status": "running", "total": total,
                "already_complete": already, "pending": len(pending),
                "processed_this_run": i, "ok": ok, "failed": failed,
                "skipped": skipped, "last_track": f"{title} - {artist}",
                "elapsed_s": time.time() - t0,
            })
        _safe_print(f"\n=== recompute-features done: ok={ok} skipped={skipped} failed={failed} pending={len(pending)} total={total} in {(time.time()-t0)/60:.1f}m ===")
        if failed_tracks:
            _safe_print("Failed tracks (audio may be corrupt):")
            for ft in failed_tracks:
                _safe_print("  " + ft)

        # library-wide z-score normalization
        acts = [r[0] for r in conn.execute(
            "SELECT activation FROM tracks WHERE activation IS NOT NULL"
        ).fetchall()]
        if len(acts) >= 2:
            mean = statistics.fmean(acts)
            std = statistics.pstdev(acts)
        else:
            mean = 50.0
            std = 0.0
        updated = 0
        for r in conn.execute("SELECT id, activation FROM tracks WHERE activation IS NOT NULL").fetchall():
            act = r[1]
            rel = 50.0 + ((act - mean) / std) * 15.0 if std > 1e-9 else 50.0
            rel = max(0.0, min(100.0, rel))
            conn.execute(
                "UPDATE tracks SET activation_relative = ?, vibe_score = ? WHERE id = ?",
                (rel, rel, r[0]),
            )
            updated += 1
        conn.commit()
        _safe_print(f"=== z-score normalized {updated} tracks: activation mean={mean:.2f} std={std:.2f} ===")
        _write_progress({
            "status": "complete", "total": total,
            "already_complete": already, "pending": len(pending),
            "processed_this_run": len(pending), "ok": ok, "failed": failed,
            "skipped": skipped, "last_track": None,
            "elapsed_s": time.time() - t0,
            "zscore_mean": mean, "zscore_std": std,
            "failed_tracks": failed_tracks,
        })
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--backfill", action="store_true", help="Download missing audio files for existing tracks")
    p.add_argument("--match-spotify", dest="match_spotify", action="store_true", help="Match tracks to Spotify IDs via ISRC/title+artist")
    p.add_argument("--recompute-features", dest="recompute_features", action="store_true",
                   help="Re-run librosa extract_full on all locally cached audio files and persist all features + derived axes + z-score-normalized activation_relative")
    p.add_argument("--limit", type=int, default=None, help="Only process the first N tracks (debug)")
    p.add_argument("--force", action="store_true", help="Recompute even for tracks whose v2 columns are already populated")
    args = p.parse_args()
    if args.match_spotify:
        run_match_spotify()
    elif args.backfill:
        run_backfill()
    elif args.recompute_features:
        run_recompute_features(limit=args.limit, force=args.force)
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
