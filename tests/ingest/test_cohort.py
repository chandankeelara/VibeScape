"""Which rows a runner pass carries (scripts/run_ingest_v2.py select_cohort).

Until 2026-10-15 the cohort was the lowest ids with ingestion_status
'pending' or any stage column 'pending'. Language never writes 'done' (a
tagging session does) and fuse waits for it, so rows that had been through
classify sat at language_status='pending' + fuse_status='pending' with
ingestion_status still 'pending': selected by every pass, actionable by no
stage. The pass processed nothing, --loop called itself drained, and every
higher id waited. On production (read-only probe, 2026-10-15) 28 such rows
sat at the head of the id order, ahead of 799 that had never entered the
pipeline: the shape add_pending() and the first pass build here.

The language and fuse stages here are the real ones. `Audio` stands in for
preview -> download -> librosa -> classify, which need the network and a
GPU: preview's entry gate in, classify's end state out (fuse armed).
"""
import numpy as np

import run_ingest_v2 as runner
from ingest_pipeline import language_tagging as lt
from ingest_pipeline.base import RowResult, Stage, STATUS_DONE, id_filter
from ingest_pipeline.fused_vector import MERT_DIM
from ingest_pipeline.stage_fuse import CURRENT_MERT_VERSION, FuseStage
from ingest_pipeline.stage_language import LanguageStage


class Audio(Stage):
    name = "audio"
    status_column = "preview_status"
    max_workers = 1

    def fetch_pending(self, conn, limit, only_ids=None):
        idf, idp = id_filter(only_ids)
        return conn.execute(
            "SELECT id FROM tracks WHERE ingestion_status = 'pending' "
            f"AND preview_status IS NULL {idf}ORDER BY id ASC LIMIT ?", (*idp, limit)).fetchall()

    def process_row(self, row):
        return RowResult(int(row["id"]), STATUS_DONE, {
            "download_status": "done", "librosa_status": "done",
            "ml_status": "done", "fuse_status": "pending"})


def stages():
    return [Audio(), LanguageStage(), FuseStage()]


def add_pending(sql, n):
    """n app-inserted rows, shaped like production's: tracks there has no
    column defaults, so every stage column starts NULL."""
    sql.executemany("INSERT INTO tracks (title, artist, spotify_id, ingestion_status, vibe_score) "
                    "VALUES (?, 'A', ?, 'pending', 0.0)",
                    [(f"T{i}", f"S{i}") for i in range(1, n + 1)])
    sql.execute("UPDATE tracks SET preview_status = NULL, download_status = NULL, "
                "librosa_status = NULL, ml_status = NULL, language_status = NULL, "
                "fuse_status = NULL, youtube_status = NULL")
    sql.commit()


def ids_where(sql, predicate):
    return [r[0] for r in sql.execute(f"SELECT id FROM tracks WHERE {predicate} ORDER BY id")]


def test_the_second_pass_advances_the_next_fifty(sql):
    add_pending(sql, 100)

    first, _ = runner.run_pass(stages(), 50)
    assert first > 0
    waiting = "ml_status = 'done' AND language_status = 'pending'"
    assert ids_where(sql, waiting) == list(range(1, 51))

    second, _ = runner.run_pass(stages(), 50)
    assert second > 0, "the pass re-selected rows no stage can act on"
    assert ids_where(sql, waiting) == list(range(1, 101))

    # All 100 now wait for a tagging session, which is the only thing that
    # can move them: the loop is genuinely drained.
    assert runner.run_pass(stages(), 50) == (0, 0)


def test_a_tagged_row_fuses_while_lower_ids_still_wait(sql):
    add_pending(sql, 100)
    runner.run_pass(stages(), 50)
    runner.run_pass(stages(), 50)
    sql.execute("INSERT INTO track_embeddings (track_id, mert_embedding, model_version) "
                "VALUES (77, ?, ?)", (np.ones(MERT_DIM, dtype=np.float32).tobytes(),
                                      CURRENT_MERT_VERSION))
    lt.tag(sql, track_id=77, language="kn")

    assert runner.select_cohort(sql, stages(), 50) == [77]
    runner.run_pass(stages(), 50)
    assert ids_where(sql, "fuse_status = 'done'") == [77]


def test_only_the_selected_stages_count(sql):
    # --stages fuse: rows that still need preview are not this pass's work.
    add_pending(sql, 3)
    assert runner.select_cohort(sql, [FuseStage()], 50) == []
    assert runner.select_cohort(sql, [Audio(), FuseStage()], 50) == [1, 2, 3]


def test_only_ids_restricts_the_union(sql):
    add_pending(sql, 10)
    assert runner.select_cohort(sql, stages(), 50, only_ids=[3, 7, 999]) == [3, 7]
    assert runner.select_cohort(sql, stages(), 50, only_ids=[]) == []
    assert runner.select_cohort(sql, stages(), 4) == [1, 2, 3, 4]
