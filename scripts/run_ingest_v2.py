"""
V2 modular ingest orchestrator.

Runs each stage across its full pending batch (concurrent I/O within a
stage), then advances to the next stage. Each stage settles
ingestion_status itself; there is no separate promote pass.
Songs move through the pipeline in waves — not one-by-one.

Wired stages, in order:
  preview   (stage_preview.py)   — resolves a 30s preview URL
  download  (stage_download.py)  — caches the audio locally
  librosa   (stage_librosa.py)   — DSP feature bank
  classify  (stage_classify.py)  — MERT: vibe/mood scalars + the 768-d vector
  language  (stage_language.py)  — the stopping point; classifies nothing
  fuse      (stage_fuse.py)      — builds the 788-d retrieval vector
  youtube   (stage_youtube.py)   — first ytsearch hit, then settles ingestion_status

Dependencies: preview -> download -> librosa -> classify -> fuse -> youtube
is one armed chain. `language` sits outside it — since 2026-10-10 it is
read off metadata rather than audio, so it needs no preview, no cached
file and no GPU, and is armed at ingest entry in parallel with preview.

A pipeline run STOPS at language. The stage classifies nothing; it only
normalises every live row into one unambiguous waiting state,
`language_status='pending'`. A Claude Code session then queries the
database for those rows, reads title/artist/album, and writes the tag plus
the `fuse_status='pending'` cascade (ingest_pipeline/language_tagging.py).
The next run sees 'done' and proceeds to fuse -> youtube.

`fuse` gates strictly on language_status='done' — 20% of the fused vector
is a language one-hot, so fusing early puts a track in the wrong region of
the similarity space rather than merely a less precise one. That makes the
tagging step load-bearing: untagged tracks never reach the DJ pool.
`python scripts/language_tags.py --status` says how many are waiting and
for how long.

Usage:
    # One pass across every stage, up to 50 rows per stage:
    python scripts/run_ingest_v2.py --batch 50

    # Loop with 30 s idle sleep between empty passes:
    python scripts/run_ingest_v2.py --loop --interval 30

    # Restrict to a subset of stages (comma-separated):
    python scripts/run_ingest_v2.py --stages preview,youtube

    # Only re-run the library-wide score recompute, no stages:
    python scripts/run_ingest_v2.py --recompute-only

    # Production Turso instead of the local SQLite file (the default,
    # --target local). Credentials come from the gitignored .env.prod, see
    # scripts/_target.py. Back up first: python scripts/backup_prod_db.py
    python scripts/run_ingest_v2.py --target prod --batch 10
    python scripts/run_ingest_v2.py --target prod --batch 50 --loop

Every log line names the target, so a prod run cannot be mistaken for a
local one.

Each pass carries one cohort: the lowest --batch ids that at least one of
the selected stages can act on, by that stage's own gate (select_cohort).

The runner re-runs the library-wide score recompute
(ingest/score_recompute.py): activation/valence from the librosa features,
activation z-scored across the catalogue into activation_relative and
vibe_score, and mood. Only librosa and classify move it — librosa writes its
inputs and adds rows to the baseline, classify overwrites the columns it
owns — so it runs only if one of them finished a track:

  * a single pass: once at the end of the pass;
  * --loop: ONCE WHEN THE LOOP DRAINS (the first pass that processes
    nothing), not after every scoring pass. Every newly scored track shifts
    the catalogue mean and std, so nearly every row's activation_relative
    moves and the skip-unchanged check saves nothing: draining 1,000 tracks
    at --batch 50 would cost 20 full ~3,800-UPDATE recomputes, about 76,000
    Turso round trips, instead of one. A failed recompute keeps its pending
    count and is retried at the next idle point.

Imports used to run it after every sync; a sync only adds unanalysed rows,
so it moved here (2026-10-13).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
# Appended, not inserted: ingest/db.py must not shadow backend/db.py.
sys.path.append(str(PROJECT_ROOT / "ingest"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from db import ensure_db, get_conn  # noqa: E402
from ingest_pipeline.stage_preview import PreviewStage  # noqa: E402
from ingest_pipeline.stage_download import DownloadStage  # noqa: E402
from ingest_pipeline.stage_classify import ClassifyStage  # noqa: E402
from ingest_pipeline.stage_youtube import YoutubeStage  # noqa: E402
from ingest_pipeline.stage_language import LanguageStage  # noqa: E402
from ingest_pipeline.stage_librosa import LibrosaStage  # noqa: E402
from ingest_pipeline.stage_fuse import FuseStage  # noqa: E402
from ingest_pipeline.base import STATUS_DONE  # noqa: E402
import score_recompute  # noqa: E402
import _target  # noqa: E402  (scripts/, next to this file)


log = logging.getLogger("vibescape.ingest.orch")

# Stages whose 'done' rows change what the score recompute produces. See the
# module docstring; fuse, youtube etc. touch none of its inputs or outputs.
SCORING_STAGES = ("librosa", "classify")


def build_stages(names: list[str]) -> list:
    all_stages: dict[str, callable] = {
        "preview":   PreviewStage,
        "download":  DownloadStage,
        "librosa":   LibrosaStage,
        "classify":  ClassifyStage,
        "language":  LanguageStage,
        "fuse":      FuseStage,
        "youtube":   YoutubeStage,
    }
    unknown = [n for n in names if n not in all_stages]
    if unknown:
        raise SystemExit(f"unknown stages: {unknown}. known: {list(all_stages)}")
    return [all_stages[n]() for n in names]


def pending_snapshot(conn) -> dict[str, int]:
    out: dict[str, int] = {}
    for col in ("preview_status", "download_status", "librosa_status",
                "ml_status", "language_status", "fuse_status",
                "youtube_status"):
        try:
            n = conn.execute(
                f"SELECT COUNT(*) FROM tracks WHERE {col} = 'pending'"
            ).fetchone()[0]
            out[col] = int(n)
        except Exception:
            out[col] = -1
    return out


def select_cohort(conn, stages: list, batch: int, only_ids=None) -> list[int]:
    """
    Pick the tracks this pass will advance: the lowest `batch` ids that at
    least one of `stages` can act on now.

    "Can act on" is each stage's own fetch_pending, whose WHERE clause
    already spells out that stage's real preconditions, so the cohort is
    the union of those ids, lowest first. That covers a track still moving
    through the chain, a finished one with a stage re-armed (a corrected
    language tag sets fuse_status='pending'), and a leftover
    language_status='whisper_done' row (LanguageStage's gate), and nothing
    else. Only the stages being run count: with --stages language,fuse a
    row waiting for preview is not this pass's work.

    It used to be a predicate of its own: ingestion_status 'pending' or
    NULL, or any stage column 'pending'. Since 2026-10-10 language never
    writes 'done' (a tagging session does) and fuse waits for it, so every
    row that had been through classify sat at language_status='pending' +
    fuse_status='pending' with ingestion_status still 'pending'. Those were
    the lowest ids, every pass selected them again, no stage could touch
    them, the pass processed nothing, and the loop declared itself drained
    while every higher id waited. Production's cohort head was 28 such rows
    on 2026-10-15.

    A pass on a non-empty cohort always processes something: its lowest id
    came from some stage's gate, and only an earlier stage acting on the
    row (which counts) can move it out of that gate.

    `only_ids` (--only-ids) restricts every gate, and so the union.
    """
    if only_ids is not None and not only_ids:
        # id_filter reads an empty list as "no restriction".
        return []
    ids: set[int] = set()
    for stage in stages:
        ids.update(int(r["id"]) for r in stage.fetch_pending(conn, batch, only_ids))
    return sorted(ids)[:batch]


def run_pass(stages: list, batch: int, only_ids=None) -> tuple[int, int]:
    """One orchestrator pass.

    Selects ONE cohort of tracks and walks it through every stage in
    order, so the same tracks advance together: preview then download
    then librosa … on the same ids, within the same pass. Stages still
    process their slice as a batch (thread pools, warm models) — the
    cohort just pins WHICH rows they are allowed to touch.

    Previously each stage independently ran its own SELECT, so a pass
    could classify one set of tracks and fuse a completely different set.
    That is not hypothetical: after a migration left fuse_status='pending'
    library-wide, fuse picked the lowest ids it could find rather than the
    tracks classify had just encoded.

    Each stage settles ingestion_status itself; there is no promote pass.

    Returns (rows processed across all stages, rows a SCORING_STAGES stage
    finished as 'done'). The second says whether a recompute is owed: at the
    end of a single pass, or — accumulated across passes — once the loop
    drains (see the module docstring for why not per pass).
    """
    conn = get_conn()
    total_processed = 0
    scored = 0
    try:
        cohort = select_cohort(conn, stages, batch, only_ids)
        if not cohort:
            return 0, 0
        log.info("pass cohort: %d tracks (ids %d..%d)",
                 len(cohort), cohort[0], cohort[-1])
        for stage in stages:
            counts = stage.run_batch(conn, batch, log, only_ids=cohort)
            total_processed += sum(counts.values())
            if stage.name in SCORING_STAGES:
                scored += int(counts.get(STATUS_DONE, 0))
    finally:
        conn.close()
    return total_processed, scored


def recompute_scores() -> bool:
    """Run the library-wide score recompute once and log its summary.

    Returns False (after logging the traceback) if it failed, so a --loop
    run keeps draining the pipeline instead of dying on a scoring error.
    """
    conn = get_conn()
    try:
        summary = score_recompute.recompute_axes_and_zscores(conn)
    except Exception:
        log.exception("score recompute failed")
        return False
    finally:
        conn.close()
    stats = summary.get("activation_stats") or {}
    log.info("score recompute: scored=%d written=%d unchanged=%d "
             "activation mean=%.2f std=%.2f moods=%s",
             summary["updated"], summary["written"], summary["unchanged"],
             stats.get("mean", float("nan")), stats.get("std", float("nan")),
             summary["mood_distribution"])
    return True


def after_pass(scored: int) -> bool:
    """Single-pass mode: recompute iff the pass finished something that
    moves scores. --loop does not use this; it recomputes once on draining."""
    if scored <= 0:
        return True
    log.info("pass scored %d rows; recomputing library scores", scored)
    return recompute_scores()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _target.add_argument(parser)
    parser.add_argument("--batch", type=int, default=50,
                        help="max rows per stage per pass (default 50)")
    parser.add_argument("--stages", type=str,
                        default="preview,download,librosa,classify,language,fuse,youtube",
                        help="comma-separated stage names (default: all seven in order)")
    parser.add_argument("--loop", action="store_true",
                        help="keep running; sleep --interval when nothing to do")
    parser.add_argument("--interval", type=int, default=30,
                        help="seconds to sleep between empty passes (default 30)")
    parser.add_argument("--only-ids", type=str, default=None,
                        help="file of tracks.id, one per line: advance only these "
                             "rows; with --loop, exit once they are idle")
    parser.add_argument("--recompute-only", action="store_true",
                        help="run only the library-wide score recompute "
                             "(activation_relative / vibe_score / mood), no stages")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    # Before anything connects; exits here if --target prod is not set up.
    where = _target.apply(args.target)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=f"%(asctime)s %(levelname)s target={args.target} %(name)s: %(message)s",
    )
    log.info("%s", where)

    ensure_db()
    if args.recompute_only:
        return 0 if recompute_scores() else 1

    stages = build_stages([s.strip() for s in args.stages.split(",") if s.strip()])

    conn = get_conn()
    try:
        snap = pending_snapshot(conn)
    finally:
        conn.close()
    log.info("startup pending: %s", snap)

    only_ids = None
    if args.only_ids:
        only_ids = [int(x) for x in Path(args.only_ids).read_text().split() if x.strip()]
        log.info("scoped to %d ids from %s", len(only_ids), args.only_ids)

    if not args.loop:
        processed, scored = run_pass(stages, args.batch, only_ids)
        log.info("pass done: processed=%d scored=%d", processed, scored)
        return 0 if after_pass(scored) else 1

    # Scored rows not yet folded into a recompute. Recomputing per pass
    # would rewrite ~every row each time (the mean moves), so it waits for
    # the loop to drain; see the module docstring.
    owed = 0
    while True:
        processed, scored = run_pass(stages, args.batch, only_ids)
        owed += scored
        if processed != 0:
            continue
        if owed > 0:
            log.info("loop drained; %d rows scored since the last recompute, "
                     "recomputing library scores", owed)
            if recompute_scores():
                owed = 0
            else:
                log.warning("score recompute owed for %d rows; retrying at the "
                            "next idle point", owed)
        if only_ids is not None:
            log.info("scoped ids idle; done")
            return 0 if owed == 0 else 1
        log.info("all stages idle; sleeping %ds", args.interval)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
