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

from .base import RowResult, Stage, STATUS_DONE, STATUS_FAILED, STATUS_NO_MATCH, iso_now


log = logging.getLogger("vibescape.ingest.youtube")


class YoutubeStage(Stage):
    name = "youtube"
    status_column = "youtube_status"
    # FINISHER. Armed by EmbeddingStage, i.e. only once preview,
    # download, classify and embedding have all succeeded. It arms
    # nothing — instead it writes ingestion_status='done' itself, the
    # transition that makes a track visible to the app.
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

    def fetch_pending(self, conn, limit: int) -> list:
        rows = conn.execute(
            "SELECT id, title, artist FROM tracks "
            # Own column armed by embedding, AND the upstream really
            # finished. The second clause is what enforces "last": local
            # rows carry a stale youtube_status='pending' from the Turso
            # pull, and without it those get picked up before the audio
            # stages have run.
            "WHERE youtube_status = 'pending' "
            "AND embedding_status = 'done' "
            "AND title IS NOT NULL AND title != '' "
            "AND artist IS NOT NULL AND artist != '' "
            "ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return list(rows)

    def process_row(self, row) -> RowResult:
        title = row["title"]
        artist = row["artist"]
        vid = self._first_hit(title, artist)
        if not vid:
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_NO_MATCH,
                fields={
                    "youtube_queried_at": iso_now(),
                    # No video found, but the track is still fully
                    # ingested and playable — a missing youtube_id only
                    # costs the video panel its fallback.
                    "ingestion_status":   "done",
                },
            )
        return RowResult(
            track_id=int(row["id"]),
            status=STATUS_DONE,
            fields={
                "youtube_id":         vid,
                "youtube_queried_at": iso_now(),
                # The finishing write: everything upstream succeeded and
                # the video id is in hand.
                "ingestion_status":   "done",
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
