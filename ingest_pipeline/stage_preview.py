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

from .base import RowResult, Stage, STATUS_DONE, STATUS_NO_MATCH, id_filter, iso_now
from .preview_providers import PreviewChain, default_chain


log = logging.getLogger("vibescape.ingest.preview")


_FETCH_COLS = (
    "id, spotify_id, apple_id, isrc, title, artist, album, "
    "artwork_url, preview_url, track_view_url, genre, duration_ms"
)


class PreviewStage(Stage):
    name = "preview"
    status_column = "preview_status"
    # Preview is an ENTRY stage: nothing upstream arms it, so it triggers
    # off ingestion_status='pending' — the only status column the app's
    # INSERT writes literally (backend/app.py). The other six rely on a
    # column DEFAULT that exists locally but not in Turso.
    arms = ("download_status",)
    # No provider had a preview: the track can never be analysed, so this
    # settles it. Was promote()'s ->no_preview rule.
    finalizes = {STATUS_NO_MATCH: "no_preview"}
    # Kept low because the iTunes Search API rate-limits aggressively
    # (~20 req/min per IP) and ItunesPreview already serializes behind
    # a global lock. More workers here would just spin waiting for the
    # lock without any throughput gain.
    max_workers = 2

    def __init__(self, chain: PreviewChain | None = None):
        self._chain = chain or default_chain(log=log)

    def fetch_pending(self, conn, limit: int, only_ids=None) -> list:
        rows = conn.execute(
            f"SELECT {_FETCH_COLS} FROM tracks "
            # Entry condition: the app queued this track and no terminal
            # verdict has been reached for it yet.
            f"WHERE ingestion_status = 'pending' "
            # Not-yet-attempted guard. NULL is the prod-Turso spelling of
            # 'pending' (no column default there); 'pending' is the local
            # one. Without this, a row whose preview is already 'done'
            # would be re-picked every pass until ml_status catches up and
            # promote() finally flips ingestion_status off 'pending'.
            f"AND (preview_status IS NULL OR preview_status = 'pending') "
            f"{id_filter(only_ids)[0]}"
            f"ORDER BY id ASC LIMIT ?",
            (*id_filter(only_ids)[1], limit),
        ).fetchall()
        return list(rows)

    def process_row(self, row) -> RowResult:
        # Already resolved — do not touch the network, and do not touch
        # the row's provenance either.
        #
        # The chain would also skip iTunes here, because SpotifyPreview
        # sits first and returns any stored preview_url. But that is an
        # accident of provider ORDER, not a guarantee: reorder
        # DEFAULT_CHAIN, or drop SpotifyPreview, and a status flip would
        # silently re-query iTunes for the whole library at ~2 req/s
        # behind a global lock. An explicit guard costs one branch.
        #
        # It also fixes a real corruption: going through the chain
        # returned source='spotify' for a URL that iTunes had supplied,
        # overwriting preview_source on every re-run. A cached row keeps
        # whatever provenance it was first given.
        existing = (row["preview_url"] or "").strip()
        if existing:
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_DONE,
                fields={"ingestion_attempted_at": iso_now()},
            )

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
