"""
V2 modular ingest orchestrator.

Runs each stage across its full pending batch (concurrent I/O within a
stage), then advances to the next stage. Each stage settles
ingestion_status itself; there is no separate promote pass.
Songs move through the pipeline in waves — not one-by-one.

Wired stages:
  preview   (ingest_pipeline/stage_preview.py)   — resolves preview_url
  classify  (ingest_pipeline/stage_classify.py)  — MERT + vibe/mood + embeddings
  youtube   (ingest_pipeline/stage_youtube.py)   — first ytsearch hit, no embed check
  language  (ingest_pipeline/stage_language.py)  — Whisper language detection

Stage dependencies:
  classify / language depend on preview_status='done'
  youtube  is independent (uses title/artist only)

Usage:
    # One pass across all four stages, up to 50 rows per stage:
    python scripts/run_ingest_v2.py --batch 50

    # Loop with 30 s idle sleep between empty passes:
    python scripts/run_ingest_v2.py --loop --interval 30

    # Restrict to a subset of stages (comma-separated):
    python scripts/run_ingest_v2.py --stages preview,youtube
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


log = logging.getLogger("vibescape.ingest.orch")


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


def select_cohort(conn, batch: int) -> list[int]:
    """
    Pick the tracks this pass will advance, oldest first.

    A cohort is 'pending' work: either never started, or part-way through
    the chain. Terminal rows (done / no_preview / *_stage_error) are
    excluded, so a pass never re-touches finished or parked tracks.
    """
    rows = conn.execute(
        "SELECT id FROM tracks "
        "WHERE ingestion_status = 'pending' OR ingestion_status IS NULL "
        "ORDER BY id ASC LIMIT ?",
        (batch,),
    ).fetchall()
    return [int(r[0]) for r in rows]


def run_pass(stages: list, batch: int) -> int:
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

    Returns total rows processed across all stages.
    """
    conn = get_conn()
    total_processed = 0
    try:
        cohort = select_cohort(conn, batch)
        if not cohort:
            return 0
        log.info("pass cohort: %d tracks (ids %d..%d)",
                 len(cohort), cohort[0], cohort[-1])
        for stage in stages:
            counts = stage.run_batch(conn, batch, log, only_ids=cohort)
            total_processed += sum(counts.values())
    finally:
        conn.close()
    return total_processed


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
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    ensure_db()
    stages = build_stages([s.strip() for s in args.stages.split(",") if s.strip()])

    conn = get_conn()
    try:
        snap = pending_snapshot(conn)
    finally:
        conn.close()
    log.info("startup pending: %s", snap)

    if not args.loop:
        processed = run_pass(stages, args.batch)
        log.info("pass done: processed=%d", processed)
        return 0

    while True:
        processed = run_pass(stages, args.batch)
        if processed == 0:
            log.info("all stages idle; sleeping %ds", args.interval)
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
