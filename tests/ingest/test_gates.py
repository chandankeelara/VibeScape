"""Each stage's gate (Stage.gate_sql), spelled once and read three ways:
fetch_pending, the runner's one-query cohort, and its startup counts.

The first version of the gate-built cohort asked every stage's
fetch_pending in turn: seven queries a pass, six scanning tracks and fuse's
reading a MERT blob per row, every 30 s of an idle --loop. And the startup
line counted `<column> = 'pending'`, which no stage acts on.
"""
import numpy as np
import pytest

import run_ingest_v2 as runner
from ingest_pipeline.fused_vector import MERT_DIM
from ingest_pipeline.stage_fuse import CURRENT_MERT_VERSION

STATUS = ("preview_status", "download_status", "librosa_status", "ml_status",
          "language_status", "fuse_status", "youtube_status")


def bare(cls):
    """A stage without its __init__, which loads librosa, yt-dlp or MERT;
    the gate and fetch_pending are class-level."""
    return object.__new__(cls)


def stages():
    return [bare(cls) for cls in runner.STAGE_CLASSES.values()]


@pytest.fixture
def rows(sql):
    """One row per stage that only that stage's gate accepts, plus `none`,
    which none does: a row waiting for its language tag."""
    def add(name, **cols):
        cur = sql.execute("INSERT INTO tracks (title, artist, spotify_id, ingestion_status, "
                          "vibe_score) VALUES (?, 'A', ?, 'pending', 0.0)", (name, name))
        tid = int(cur.lastrowid)
        values = {c: None for c in STATUS}          # production's shape: no defaults
        values.update(cols)
        sql.execute(f"UPDATE tracks SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
                    (*values.values(), tid))
        return tid

    done = {c: "done" for c in STATUS}
    ids = {
        "preview": add("p", language_status="pending"),
        "download": add("d", preview_status="done", download_status="pending",
                        preview_url="https://p", language_status="pending"),
        "librosa": add("lb", preview_status="done", download_status="done",
                       librosa_status="pending", audio_path="a.m4a", language_status="pending"),
        "classify": add("c", preview_status="done", download_status="done",
                        librosa_status="done", ml_status="pending", audio_path="a.m4a",
                        language_status="pending"),
        "language": add("lg", preview_status="done"),
        "fuse": add("f", **{**done, "fuse_status": "pending", "youtube_status": None}),
        "youtube": add("y", **{**done, "youtube_status": "pending"}),
        "none": add("n", **{**done, "language_status": "pending", "fuse_status": "pending",
                            "youtube_status": None}),
    }
    for name in ("fuse", "none"):
        sql.execute("INSERT INTO track_embeddings (track_id, mert_embedding, model_version) "
                    "VALUES (?, ?, ?)", (ids[name], np.ones(MERT_DIM, np.float32).tobytes(),
                                         CURRENT_MERT_VERSION))
    sql.commit()
    return ids


def test_each_gate_accepts_exactly_its_row(sql, rows):
    for name, cls in runner.STAGE_CLASSES.items():
        assert [int(r["id"]) for r in bare(cls).fetch_pending(sql, 50)] == [rows[name]], name


def test_the_cohort_is_one_ids_only_query_over_every_gate(sql, rows):
    seen = []

    class Spy:
        def execute(self, q, params=()):
            seen.append(q)
            return sql.execute(q, params)

    cohort = runner.select_cohort(Spy(), stages(), 50)
    assert cohort == sorted(tid for name, tid in rows.items() if name != "none")
    assert len(seen) == 1 and seen[0].startswith("SELECT t.id FROM tracks t WHERE")


def test_the_startup_counts_are_the_gates(sql, rows):
    # `none` reads language_status and fuse_status 'pending', which the old
    # counts reported as work, and no stage can act on it.
    assert runner.pending_snapshot(sql, stages()) == {name: 1 for name in runner.STAGE_CLASSES}


def test_an_empty_cohort_matches_nothing(sql, rows):
    # id_filter([]) used to mean "no restriction", so every caller had to
    # guard it (select_cohort did, with a special case now gone).
    assert bare(runner.STAGE_CLASSES["preview"]).fetch_pending(sql, 50, only_ids=[]) == []
    assert runner.select_cohort(sql, stages(), 50, only_ids=[]) == []
