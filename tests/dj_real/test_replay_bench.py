"""scripts/dj_eval/replay_bench.py --w-genre and genre@10, on a small
synthetic copy (three languages, a few sounds, most tracks genre-tagged, one
real user's DJ plays). Runs the benchmark's own main(); no server, no
production data.

The point of G0: W_GENRE = 0 is fused_v2 padded with zeros, so every number
the benchmark reports for it must equal fused_v2's (AZ). If it did not, a
change measured on the grid would be partly the genre block and partly
something else -- the language block's protection lost at the new width,
say, or a different norm."""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from dj_eval import replay_bench as rb  # noqa: E402
from dj_eval.genres import genre_share, primary_genre  # noqa: E402
from ingest_pipeline import fused_vector as fv  # noqa: E402
from ingest_pipeline.stage_fuse import CURRENT_MERT_VERSION  # noqa: E402

GENRE_OF_SOUND = ["film_melody", "film_mass", "pop", "hiphop_rap"]


@pytest.fixture
def copy(sql, db):
    """200 analysed tracks (80 en, 70 kn, 50 hi) over four sounds; 3 in 4
    genre-tagged with their sound's genre; one user who owns them all and
    played 60 DJ picks after a search."""
    rng = np.random.default_rng(5)
    sounds = [rng.normal(size=fv.MERT_DIM) for _ in GENRE_OF_SOUND]
    ids = []
    for i, lang in enumerate(["en"] * 80 + ["kn"] * 70 + ["hi"] * 50):
        snd = i % len(sounds)
        tagged = i % 4 != 0
        # mostly the sound's genre, but not always: then only the genre
        # block can tell them apart
        genre = GENRE_OF_SOUND[snd] if i % 5 else GENRE_OF_SOUND[(snd + 1) % len(sounds)]
        scores = {genre: 100} if tagged else None
        if tagged and i % 3 == 0:
            scores["folk"] = 50
        cols = {c: float(rng.uniform(*fv.SCALAR_RANGE[c])) for c in fv.SCALAR_COLS}
        cur = sql.execute(
            f"INSERT INTO tracks (title, artist, album, spotify_id, duration_ms, ingestion_status, "
            f"vibe_score, vibe_score_ml, language, language_status, genre_scores, genre_status, "
            f"{', '.join(cols)}) VALUES (?, ?, 'Album', ?, 200000, 'done', 50, ?, ?, 'done', ?, ?, "
            f"{', '.join('?' * len(cols))})",
            (f"Song {i} {secrets.token_hex(3)}", f"Artist {i % 23}", secrets.token_hex(11),
             float(rng.uniform(0, 100)), lang, json.dumps(scores) if scores else None,
             "done" if tagged else "pending", *cols.values()))
        tid = int(cur.lastrowid)
        mert = (sounds[snd] + 0.7 * rng.normal(size=fv.MERT_DIM)).astype(np.float32)
        sql.execute("INSERT INTO track_embeddings (track_id, mert_embedding, fused_embedding, "
                    "model_version) VALUES (?, ?, ?, ?)",
                    (tid, mert.tobytes(), rng.normal(size=788).astype(np.float32).tobytes(),
                     CURRENT_MERT_VERSION))
        ids.append(tid)
    uid = int(sql.execute("INSERT INTO users (display_name) VALUES ('listener')").lastrowid)
    sql.executemany("INSERT INTO user_tracks (user_id, track_id) VALUES (?, ?)",
                    [(uid, t) for t in ids])
    ts = 1_760_000_000_000
    order = rng.permutation(ids).tolist()
    for k, tid in enumerate(order[:61]):
        ts += 300_000
        source = "search" if k == 0 else "dj"
        sql.execute("INSERT INTO track_events (user_id, track_id, type, source, client_ts) "
                    "VALUES (?, ?, 'play_start', ?, ?)", (uid, tid, source, ts))
        done = k == 0 or rng.random() < 0.5
        sql.execute("INSERT INTO track_events (user_id, track_id, type, reason, position_ms, "
                    "duration_ms, client_ts) VALUES (?, ?, 'play_end', ?, ?, 200000, ?)",
                    (uid, tid, "completed" if done else "skipped",
                     200000 if done else 15000, ts + 1000))
    sql.commit()
    return db


def test_primary_genre_is_the_top_score_ties_to_taxonomy_order():
    assert primary_genre('{"folk":100,"film_melody":100}') == "film_melody"
    assert primary_genre({"folk": 100, "pop": 50}) == "folk"
    assert primary_genre({"pop": 100}, "pending") is None
    assert primary_genre(None) is None and primary_genre("{bad") is None
    assert primary_genre({"not_a_genre": 100}) is None


def test_genre_share_is_over_tagged_seeds_only():
    prim = {1: "pop", 2: "pop", 3: "folk", 4: None}
    assert genre_share(1, [2, 3, 4, 2], prim) == 0.5
    assert genre_share(4, [1, 2], prim) is None


def test_w_genre_zero_reproduces_fused_v2_on_every_number(copy, tmp_path, capsys):
    listeners = tmp_path / "listeners"
    listeners.mkdir()
    out = tmp_path / "out"
    assert rb.main(["--db", str(copy), "--listeners", str(listeners),
                    "--w-genre", "0,0.15", "--out", str(out)]) == 0
    results = json.loads(next(out.glob("replay_bench_*.json")).read_text(encoding="utf-8"))
    by = {r["space"]: r for r in results}
    assert set(by) == {"AZ", "G0", "G0.15"}

    def numbers(r):
        return json.dumps({k: v for k, v in r.items() if k != "space"}, sort_keys=True)
    assert numbers(by["G0"]) == numbers(by["AZ"])
    # and a genre weight is a different space, which holds genre better
    assert by["G0.15"]["genre10_real"] > by["AZ"]["genre10_real"]
    # the metric ran: real-user moments with a tagged seed were scored
    assert by["G0.15"]["n_genre"]["real"] > 0
    assert 0.0 <= by["G0.15"]["genre10_real"] <= 1.0
    assert by["G0.15"]["served_auc_real"] == by["G0.15"]["served_auc_real"]   # not NaN
    printed = capsys.readouterr().out
    assert "genre@10 real (n)" in printed and "150 of 200 tracks genre-tagged" in printed


def test_a_served_track_that_was_not_eligible_has_no_rank():
    """A real user's autoplay can serve the seed again (a repeat). It is
    not in the ranked set, so it has no rank; the rank array was np.empty
    and such a moment read uninitialised memory as its rank, which moved
    real-user AUC from run to run."""
    rng = np.random.default_rng(3)
    X = rng.normal(size=(30, 8))
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    ids = list(range(1, 31))
    rk = rb.Ranker(ids, X, {t: "en" for t in ids})
    m = {"events": [{"id": 5, "action": "completed", "played_ratio": 1.0}],
         "seed": 5, "exclude": {7}, "user": None}
    P = rk.pool(m)
    _top, rank_of = rk.final(m, P, None)
    assert rank_of(5) is None and rank_of(7) is None
    assert sorted(rank_of(t) for t in ids if t not in (5, 7)) == list(range(28))
