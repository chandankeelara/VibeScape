"""
YouTube ID resolution stage — first search hit, no playability check.

Per user directive: take the first ytsearch5 result and store it. Skip
the embed / age / availability filters the interactive endpoint uses.
Bad IDs are cheap to replace later; missing IDs cost nothing more than
a video-panel fallback.

Independent of the preview stage — YouTube resolution doesn't need audio.
"""
from __future__ import annotations

import logging

from .base import (RowResult, Stage, STATUS_DONE, STATUS_FAILED,
                   STATUS_NO_MATCH, id_filter, iso_now)


log = logging.getLogger("vibescape.ingest.youtube")


class YoutubeStage(Stage):
    name = "youtube"
    status_column = "youtube_status"
    finalizes = {STATUS_DONE: "done", STATUS_NO_MATCH: "done"}
    # FINISHER. Armed by EmbeddingStage, i.e. only once preview,
    # download, classify and embedding have all succeeded. It arms
    # nothing — instead it settles ingestion_status='done', the
    # transition that makes a track visible to the app. A no_match
    # settles it too: a missing video id only costs the video panel its
    # fallback, it does not make the track any less ingested.
    #
    # It needs no audio (title/artist only) so it could run anywhere,
    # but running it last means a track is never visible half-ingested:
    # by the time it reads 'done' it has a preview, a cached file,
    # scalar predictions, an embedding and a video id.
    # yt-dlp searches are fairly slow per call (~1-3s); parallelize aggressively.
    max_workers = 6

    def __init__(self):
        try:
            from yt_dlp import YoutubeDL  # noqa: F401
        except ImportError as e:
            raise SystemExit(f"YoutubeStage requires yt-dlp: {e}")

    def fetch_pending(self, conn, limit: int, only_ids=None) -> list:
        rows = conn.execute(
            "SELECT id, title, artist, youtube_id FROM tracks "
            # Armed by embedding, but the gate spells out EVERY upstream
            # stage rather than relying on transitivity. embedding_status
            # ='done' alone implies preview -> download -> ml, but NOT
            # language: that is a parallel branch armed by download, so a
            # failed language would have slipped past and let a broken
            # track reach 'done'.
            #
            # Spelling it out is also what enforces "last": local rows
            # carry a stale youtube_status='pending' forced by the Turso
            # pull, and on its own that would let youtube run before any
            # audio stage had.
            "WHERE youtube_status = 'pending' "
            "AND preview_status = 'done' "
            "AND download_status = 'done' "
            "AND librosa_status = 'done' "
            "AND ml_status = 'done' "
            "AND fuse_status = 'done' "
            # Language, strictly. It used to be the one stage whose
            # success was not spelled 'done' — Whisper stopped at
            # 'whisper_done' and could return 'no_match'. Neither state
            # has a producer since language became a queue-answered
            # metadata question (2026-10-10); "no language" is now an
            # explicit clear that writes language=NULL with
            # language_status='done'. Redundant with fuse's own gate, and
            # kept anyway: this gate's whole job is to spell out every
            # upstream stage rather than trust transitivity.
            "AND language_status = 'done' "
            "AND title IS NOT NULL AND title != '' "
            "AND artist IS NOT NULL AND artist != '' "
            f"{id_filter(only_ids)[0]}"
            "ORDER BY id ASC LIMIT ?",
            (*id_filter(only_ids)[1], limit),
        ).fetchall()
        return list(rows)

    def process_row(self, row) -> RowResult:
        # Already resolved — skip the lookup entirely. Same rule as
        # PreviewStage and DownloadStage: a status flip must never cost a
        # network round-trip for something already on the row.
        #
        # This one is the most expensive to get wrong: _first_hit runs
        # two yt-dlp ytsearch queries per track, so re-resolving a full
        # library is thousands of scrapes for ids we already hold.
        existing = (row["youtube_id"] or "").strip()
        if existing:
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_DONE,
                fields={"ingestion_attempted_at": iso_now()},
            )

        title = row["title"]
        artist = row["artist"]
        vid = self._first_hit(title, artist)
        if not vid:
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_NO_MATCH,
                fields={
                    "youtube_queried_at": iso_now(),
                },
            )
        return RowResult(
            track_id=int(row["id"]),
            status=STATUS_DONE,
            fields={
                "youtube_id":         vid,
                "youtube_queried_at": iso_now(),
            },
        )

    @staticmethod
    def _first_hit(title: str, artist: str) -> str | None:
        """Two ytsearch queries, take the first 11-char video id. No
        embed/age/availability filtering."""
        from yt_dlp import YoutubeDL

        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "noplaylist": True,
        }
        queries = [
            f'ytsearch1:{title} {artist}',
            f'ytsearch1:{title}',
        ]
        for q in queries:
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(q, download=False)
            except Exception as e:
                log.warning("yt-dlp query failed %r: %s", q, e)
                continue
            entries = info.get("entries") if isinstance(info, dict) else None
            if not entries:
                continue
            for entry in entries:
                vid = entry.get("id") if isinstance(entry, dict) else None
                if isinstance(vid, str) and len(vid) == 11:
                    return vid
        return None
