"""
Find tracks that were given ANOTHER song's audio, wipe everything derived
from it, and re-arm them for the offline pipeline. LOCAL DB only.

    python scripts/reset_shared_previews.py            # dry-run, writes the report
    python scripts/reset_shared_previews.py --apply

WHY
---
Until 2026-10-11 the iTunes matcher fell back to "first result with a
preview", so a search for one song could return a different song from the
same album. Found as 50 groups / 111 tracks with byte-identical MERT vectors
under different titles — the DJ served them to each other at cosine 1.000.
The matcher is fixed (ingest_pipeline/preview_providers.py); this clears
what it already wrote.

WHICH TRACKS
------------
Tracks are grouped when they share a preview_url OR a byte-identical
mert_embedding. A group whose members all name the same recording (the same
song stored twice) is left alone: the shared audio is correct there. Every
other group is reset IN FULL, including the member that may own the audio —
telling which one does is not reliable, and re-matching it costs one iTunes
call. Rows titled '?' are junk and skipped.

WHAT A RESET DOES
-----------------
Re-runs short-circuit on preview_url and on the cached file
(ingest_pipeline/README.md, "Re-runs are cheap"), so flipping statuses alone
would rebuild the same wrong vector. This clears the preview and its
Apple-side ids, deletes the cached audio, nulls the DSP and model outputs,
deletes the track_embeddings row, and sets ingestion_status='pending' with
every stage column NULL except language. language / language_status are
kept: the tag comes from the title, not the audio, so it is still right,
and keeping 'done' lets the row fuse without waiting for a tagging session.
youtube_id is kept for the same reason (searched by title).

Then:
    VIBESCAPE_ML_MODE=local VIBESCAPE_ITUNES_MIN_GAP=2 VIBESCAPE_ITUNES_RETRIES=7 \
    VIBESCAPE_ITUNES_BACKOFF=5 python scripts/run_ingest_v2.py --batch 30 --loop \
        --only-ids data/_reingest_shared_previews_ids.txt
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from ingest_pipeline.preview_providers import artists_match, titles_match  # noqa: E402

_DB = _REPO / "data" / "vibescape.db"
_IDS_OUT = _REPO / "data" / "_reingest_shared_previews_ids.txt"
_REPORT_OUT = _REPO / "data" / "_reingest_shared_previews_report.json"

# Everything derived from the audio: the tracks columns the librosa and
# classify stages write.
_DERIVED = [
    "tempo", "tempo_stability", "onset_rate", "energy_mean", "energy",
    "energy_std", "brightness", "bandwidth", "rolloff", "spectral_contrast",
    "flatness", "zcr", "timbre_variability", "valence_mode", "tonnetz_std",
    "acousticness", "mfcc_json", "chroma_mean_json",
    "activation", "valence", "activation_relative", "vibe_score", "mood",
    "energy_pred", "danceability_pred", "valence_pred", "vibe_score_ml",
    "model_version", "classification_source", "ml_predicted_at",
]
# Written by the wrong iTunes hit. album / artwork / genre / duration are
# only filled when Spotify left them empty, so they are mostly Spotify's
# and are kept.
_PREVIEW = ["preview_url", "preview_source", "apple_id", "track_view_url", "audio_path"]
_STAGES = ["preview_status", "download_status", "librosa_status", "ml_status",
           "fuse_status", "youtube_status", "embedding_status"]


def _groups(conn) -> list[list[sqlite3.Row]]:
    rows = conn.execute(
        "SELECT t.id, t.spotify_id, t.title, t.artist, t.preview_url, t.audio_path, "
        "       t.ingestion_status, te.mert_embedding AS m "
        "FROM tracks t LEFT JOIN track_embeddings te ON te.track_id = t.id "
        "WHERE t.title != '?'").fetchall()
    by_id = {r["id"]: r for r in rows}
    parent = {r["id"]: r["id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    keys = defaultdict(list)
    for r in rows:
        if r["preview_url"]:
            keys["u:" + r["preview_url"]].append(r["id"])
        if r["m"]:
            keys["m:" + hashlib.md5(r["m"]).hexdigest()].append(r["id"])
    for ids in keys.values():
        for i in ids[1:]:
            parent[find(i)] = find(ids[0])
    comp = defaultdict(list)
    for i in parent:
        comp[find(i)].append(by_id[i])
    return [g for g in comp.values() if len(g) > 1]


def _same_recording(g) -> bool:
    a = g[0]
    return all(titles_match(a["title"], b["title"]) and artists_match(a["artist"], b["artist"])
               for b in g[1:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", type=Path, default=None,
                    help="read another SQLite file (e.g. a prod backup); dry-run only")
    args = ap.parse_args()
    if args.db and args.apply:
        ap.error("--db is read-only; --apply resets the local DB only")
    db = args.db or _DB

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True) if args.db else sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    groups = _groups(conn)
    bad = [g for g in groups if not _same_recording(g)]
    dupes = [g for g in groups if _same_recording(g)]
    targets = sorted({r["id"] for g in bad for r in g})

    print(f"groups sharing audio: {len(groups)}  "
          f"(same song stored twice, kept: {len(dupes)};  "
          f"different songs, reset: {len(bad)} groups / {len(targets)} tracks)")
    for g in sorted(bad, key=len, reverse=True):
        print("  " + " / ".join(f"{r['title']} [{r['artist']}]" for r in g))

    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "reset": [[{"id": r["id"], "spotify_id": r["spotify_id"], "title": r["title"],
                          "artist": r["artist"], "old_preview_url": r["preview_url"]}
                         for r in g] for g in bad],
              "kept_duplicates": [[{"id": r["id"], "title": r["title"], "artist": r["artist"]}
                                   for r in g] for g in dupes]}
    # A --db run names its outputs after the file it read, so a dry-run on a
    # prod backup never overwrites the local run's id list.
    suffix = f"_{Path(db).stem}" if args.db else ""
    report_out = _REPORT_OUT.with_name(_REPORT_OUT.stem + suffix + ".json")
    ids_out = _IDS_OUT.with_name(_IDS_OUT.stem + suffix + ".txt")
    report_out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    ids_out.write_text("\n".join(map(str, targets)) + "\n")
    print(f"\nwrote {ids_out.name} and {report_out.name}")

    if not args.apply:
        print("dry-run — pass --apply to reset. Nothing was modified.")
        return 0

    backup = _DB.with_name(f"vibescape.db.pre-reset-previews-{time.strftime('%Y%m%dT%H%M%S')}")
    conn.close()
    shutil.copy2(_DB, backup)
    print(f"backup: {backup.name}")
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row

    files = 0
    for r in conn.execute(
            f"SELECT spotify_id, audio_path FROM tracks WHERE id IN ({','.join('?' * len(targets))})",
            targets).fetchall():
        for p in {r["audio_path"], f"data/audio/{r['spotify_id']}.m4a"}:
            if p and (_REPO / p).is_file():
                (_REPO / p).unlink()
                files += 1

    # vibe_score is NOT NULL; 0.0 is what the app's own pending INSERT
    # writes (backend/services/library_ingest.py _link_track).
    nulls = ", ".join(f"{c} = 0.0" if c == "vibe_score" else f"{c} = NULL"
                      for c in _DERIVED + _PREVIEW + _STAGES)
    ph = ",".join("?" * len(targets))
    conn.execute(f"UPDATE tracks SET {nulls}, ingestion_status = 'pending', "
                 f"ingestion_error = NULL WHERE id IN ({ph})", targets)
    emb = conn.execute(f"DELETE FROM track_embeddings WHERE track_id IN ({ph})", targets).rowcount
    conn.commit()
    print(f"reset {len(targets)} tracks, deleted {emb} embeddings and {files} audio files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
