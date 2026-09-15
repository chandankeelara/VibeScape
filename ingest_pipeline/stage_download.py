"""
Audio download / cache stage.

Once PreviewStage has resolved a preview URL, DownloadStage fetches the
file to data/audio/<spotify_id><ext> and records the relative path in
tracks.audio_path. Every downstream audio-consuming stage (classify,
language, embedding) then prefers the local file — one preview download
per song ever, not per stage per pass.

Terminal states:
    'done'     — audio_path set + file present on disk
    'no_match' — URL responded but the fetch failed after retries
                 (preview_status stays 'done'; downstream stages that
                 can't work without local audio will fall back to
                 downloading from URL directly)
    'failed'   — unexpected exception

Blocks on preview_status='done' + preview_url present.
"""
from __future__ import annotations

import logging
import os
import urllib.request
from pathlib import Path

from .base import RowResult, Stage, STATUS_DONE, STATUS_FAILED, STATUS_NO_MATCH, iso_now


log = logging.getLogger("vibescape.ingest.download")


_REPO_ROOT = Path(__file__).resolve().parents[1]
_AUDIO_DIR = _REPO_ROOT / "data" / "audio"
_AUDIO_REL = "data/audio"

_TIMEOUT_S = 30


def _suffix_for(url: str) -> str:
    """Pick a filename extension based on URL hints.

    iTunes previews are AAC in .m4a; Deezer previews are MP3; Spotify
    previews are MP3. Default to .mp3 when unsure — MERT / whisper /
    librosa all sniff container via ffmpeg regardless."""
    if ".m4a" in url or "plus.aac" in url:
        return ".m4a"
    return ".mp3"


def _local_path_for(spotify_id: str, url: str) -> Path:
    return _AUDIO_DIR / f"{spotify_id}{_suffix_for(url)}"


class DownloadStage(Stage):
    name = "download"
    status_column = "download_status"
    # Network I/O only, no GPU — safe to parallelize. iTunes AudioPreview
    # CDN doesn't rate-limit downloads the way the Search API does.
    max_workers = 8

    def __init__(self):
        _AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    def fetch_pending(self, conn, limit: int) -> list:
        rows = conn.execute(
            "SELECT id, spotify_id, preview_url, audio_path "
            "FROM tracks "
            "WHERE download_status = 'pending' "
            "AND preview_status = 'done' "
            "AND preview_url IS NOT NULL AND preview_url != '' "
            "AND spotify_id IS NOT NULL AND spotify_id != '' "
            "ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return list(rows)

    def process_row(self, row) -> RowResult:
        spotify_id = row["spotify_id"]
        url = row["preview_url"]
        target = _local_path_for(spotify_id, url)

        # Already present on disk (e.g. legacy audio from earlier ingest
        # runs) — just point audio_path at it and mark done.
        if target.exists() and target.stat().st_size > 1024:
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_DONE,
                fields={
                    "audio_path": f"{_AUDIO_REL}/{target.name}",
                    "ingestion_attempted_at": iso_now(),
                },
            )

        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as r:
                data = r.read()
        except Exception as e:
            log.warning("download failed for %s: %s", spotify_id, e)
            msg = f"download: {e.__class__.__name__}: {e}"[:400]
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_NO_MATCH,
                fields={"ingestion_attempted_at": iso_now(),
                        "ingestion_error": msg},
                error=msg,
            )
        if not data or len(data) < 1024:
            msg = f"download returned {len(data) if data else 0} bytes"
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_NO_MATCH,
                fields={"ingestion_attempted_at": iso_now(),
                        "ingestion_error": msg},
                error=msg,
            )
        try:
            _AUDIO_DIR.mkdir(parents=True, exist_ok=True)
            # Write atomically via .part rename to avoid a half-written
            # file being read by a concurrent stage.
            part = target.with_suffix(target.suffix + ".part")
            part.write_bytes(data)
            os.replace(part, target)
        except Exception as e:
            log.warning("write failed for %s: %s", spotify_id, e)
            msg = f"write: {e.__class__.__name__}: {e}"[:400]
            return RowResult(
                track_id=int(row["id"]),
                status=STATUS_FAILED,
                fields={"ingestion_attempted_at": iso_now(),
                        "ingestion_error": msg},
                error=msg,
            )

        return RowResult(
            track_id=int(row["id"]),
            status=STATUS_DONE,
            fields={
                "audio_path": f"{_AUDIO_REL}/{target.name}",
                "ingestion_attempted_at": iso_now(),
            },
        )


def resolve_audio_path(audio_path: str | None) -> Path | None:
    """Resolve a tracks.audio_path value to an absolute Path.

    Public helper — used by classify / language / embedding stages to
    check whether the local file exists before falling back to the URL.
    Returns None if the path is missing, empty, or points at a file that
    doesn't exist.
    """
    if not audio_path:
        return None
    p = Path(audio_path)
    if not p.is_absolute():
        p = _REPO_ROOT / p
    if p.exists() and p.is_file() and p.stat().st_size > 1024:
        return p
    return None
