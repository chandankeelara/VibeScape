"""
Preview URL resolution stage.

Fetches rows with preview_status='pending', asks the provider chain for
a preview URL, and writes back:
  - preview_url    (only if chain returned one)
  - preview_source ('spotify' | 'itunes' | 'deezer_isrc' | 'deezer_search' | None)
  - apple_id / genre / track_view_url / artwork_url / album / duration_ms
    (via COALESCE-in-python: only overwritten if currently missing)
  - preview_status ('done' if a URL was found, 'no_match' if every
                    provider returned None)
"""
from __future__ import annotations

import logging

from .base import RowResult, Stage, STATUS_DONE, STATUS_NO_MATCH, iso_now
from .preview_providers import PreviewChain, default_chain


log = logging.getLogger("vibescape.ingest.preview")


_FETCH_COLS = (
    "id, spotify_id, apple_id, isrc, title, artist, album, "
    "artwork_url, preview_url, track_view_url, genre, duration_ms"
)


class PreviewStage(Stage):
    name = "preview"
    status_column = "preview_status"
    max_workers = 8

    def __init__(self, chain: PreviewChain | None = None):
        self._chain = chain or default_chain(log=log)

    def fetch_pending(self, conn, limit: int) -> list:
        rows = conn.execute(
            f"SELECT {_FETCH_COLS} FROM tracks "
            f"WHERE preview_status = 'pending' "
            f"ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return list(rows)

    def process_row(self, row) -> RowResult:
        track = {
            "title":       row["title"],
            "artist":      row["artist"],
            "isrc":        row["isrc"],
            "preview_url": row["preview_url"],
        }
        hit = self._chain.resolve(track)
        if not hit:
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_NO_MATCH,
                fields={
                    "ingestion_attempted_at": iso_now(),
                },
            )
        # Fill missing metadata from the hit ONLY when the row lacks it.
        # Never overwrite Spotify-side fields the sync already provided.
        fields: dict = {
            "preview_url":    hit.url,
            "preview_source": hit.source,
            "ingestion_attempted_at": iso_now(),
        }
        if not row["apple_id"] and hit.apple_id:
            fields["apple_id"] = hit.apple_id
        if not row["album"] and hit.album:
            fields["album"] = hit.album
        if not row["genre"] and hit.genre:
            fields["genre"] = hit.genre
        if not row["artwork_url"] and hit.artwork_url:
            fields["artwork_url"] = hit.artwork_url
        if not row["track_view_url"] and hit.track_view_url:
            fields["track_view_url"] = hit.track_view_url
        if not row["duration_ms"] and hit.duration_ms:
            fields["duration_ms"] = hit.duration_ms
        return RowResult(
            track_id=int(row["id"]),
            status=STATUS_DONE,
            fields=fields,
        )
