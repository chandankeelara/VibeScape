"""
VibeScape modular ingest pipeline.

Four independent stages operate on the `tracks` table, each gated by its
own status column:

    preview_status   → resolve a playable preview URL (Spotify/iTunes/Deezer)
    ml_status        → MERT embedding + vibe/mood/language-agnostic scores
    youtube_status   → first YouTube search hit (best-effort, no embed check)
    language_status  → Whisper language detection (best-effort)

An orchestrator (scripts/run_ingest_v2.py) fetches the pending set for
each stage, dispatches work concurrently (I/O-bound), then advances to
the next stage. `ingestion_status` is derived: a row is 'done' when
preview_status='done' AND ml_status='done'; 'no_preview' when
preview_status='no_match'. See ingest_pipeline/promote.py.

Each stage writes only its own columns + its own status column, so
stages compose freely and can later be distributed across workers or
shards without changing the interface.
"""
