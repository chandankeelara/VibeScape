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
The next run sees 'done' and proceeds to fuse → youtube.

`fuse` gates strictly on language_status='done' — 20% of the fused vector
is a language one-hot, so fusing early puts a track in the wrong region of
the similarity space rather than merely a less precise one. That makes the
tagging step load-bearing: untagged tracks never reach the DJ pool.
`python scripts/language_tags.py --status` says how many are waiting and
for how long.

Usage:
    # One pass across all four stages, up to 50 rows per stage:
    python scripts/run_ingest_v2.py --batch 50

    # Loop with 30 s idle sleep between empty passes:
    python scripts/run_ingest_v2.py --loop --interval 30

    # Restrict to a subset of stages (comma-separated):
    python scripts/run_ingest_v2.py --stages preview,youtube

    # Only re-run the library-wide score recompute, no stages:
    python scripts/run_ingest_v2.py --recompute-only

After any pass in which librosa or classify finished at least one track, the
runner re-runs the library-wide score recompute (ingest/score_recompute.py):
activation/valence from the librosa features, activation z-scored across the
catalogue into activation_relative and vibe_score, and mood. Those are the
only two stages that move it — librosa writes its inputs and adds rows to the
baseline, classify overwrites the columns it owns. Imports used to run it
after every sync; a sync only adds unanalysed rows, so it moved here
(2026-10-13).
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


def select_cohort(conn, batch: int, only_ids=None) -> list[int]:
    """
    Pick the tracks this pass will advance, oldest first.

    Two kinds of work qualify:

      1. Tracks still moving through the chain — ingestion_status 'pending'
         or NULL.
      2. Tracks that are already 'done' but have had a stage RE-ARMED.
         Correcting a language tag sets fuse_status='pending' on a finished
         row; without this clause the orchestrator could not see it and the
         pass reported processed=0 while the corrections sat unapplied.

    Terminal rows with nothing armed are excluded, so a pass never
    re-touches genuinely finished or parked tracks.
    """
    stage_cols = ("preview_status", "download_status", "librosa_status",
                  "ml_status", "language_status", "fuse_status",
                  "youtube_status")
    armed = " OR ".join(f"{c} = 'pending'" for c in stage_cols)
    # Legacy term: 'whisper_done' was the retired Whisper stage's terminal
    # state (no producer since 2026-10-10). Those rows are usually
    # ingestion_status='done' with nothing else armed, so without this they
    # could never enter a cohort, LanguageStage could never normalise them
    # to 'pending', and no tagging session would ever see them — while
    # fuse, which now requires language_status='done' exactly, would refuse
    # to re-fuse them. One term keeps them reachable.
    armed += " OR language_status = 'whisper_done'"

    # --only-ids pins the run to a hand-picked set (a re-ingest of suspect
    # rows) so it does not also drain whatever else happens to be pending.
    scope = ""
    if only_ids is not None:
        scope = f"AND id IN ({','.join(str(int(i)) for i in only_ids) or 'NULL'}) "
    rows = conn.execute(
        "SELECT id FROM tracks "
        "WHERE (ingestion_status = 'pending' OR ingestion_status IS NULL "
        f"   OR {armed}) "
        f"{scope}"
        "ORDER BY id ASC LIMIT ?",
        (batch,),
    ).fetchall()
    return [int(r[0]) for r in rows]


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
    finished as 'done') — the second says whether to recompute scores.
    """
    conn = get_conn()
    total_processed = 0
    scored = 0
    try:
        cohort = select_cohort(conn, batch, only_ids)
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
    """Recompute scores iff this pass finished something that moves them."""
    if scored <= 0:
        return True
    log.info("pass scored %d rows; recomputing library scores", scored)
    return recompute_scores()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=50,
                        help="max rows per stage per pass (default 50)")
    parser.add_argument("--stages", type=str,
                        default="preview,download,librosa,classify,language,fuse,youtube",
                        help="comma-separated stage names (default: all six in order)")
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

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

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

    while True:
        processed, scored = run_pass(stages, args.batch, only_ids)
        after_pass(scored)
        if processed == 0 and only_ids is not None:
            log.info("scoped ids idle; done")
            return 0
        if processed == 0:
            log.info("all stages idle; sleeping %ds", args.interval)
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
