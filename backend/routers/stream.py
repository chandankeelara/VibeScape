"""Audio streaming from the local audio directory, with HTTP Range support.

Auth is require_user_stream, which alone also accepts ?token= (an <audio>
element cannot send headers); see deps.py. Paths are resolved against
bootstrap.PROJECT_ROOT, read at call time because tests patch it."""
import re
from pathlib import Path
from typing import Optional

import bootstrap  # first: .env, sys.path (see bootstrap.py)

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

# Through the module, never `from db import get_conn` (conftest.use_conn).
import db
from deps import require_user_stream
from routers import Router

router = Router()


_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _resolve_audio_path(audio_path: str) -> Optional[Path]:
    if not audio_path:
        return None
    p = Path(audio_path)
    if not p.is_absolute():
        p = bootstrap.PROJECT_ROOT / p
    try:
        p = p.resolve()
    except OSError:
        return None
    try:
        p.relative_to(bootstrap.PROJECT_ROOT.resolve())
    except ValueError:
        return None
    return p


def _stream_audio_row(row, request: Request):
    if not row or not row["audio_path"]:
        raise HTTPException(status_code=404, detail="audio not found")

    audio_file = _resolve_audio_path(row["audio_path"])
    if not audio_file or not audio_file.exists() or not audio_file.is_file():
        raise HTTPException(status_code=404, detail="audio file missing on disk")

    file_size = audio_file.stat().st_size
    range_header = request.headers.get("range") or request.headers.get("Range")

    ext = audio_file.suffix.lower()
    media_type = "audio/mpeg" if ext == ".mp3" else "audio/mp4"

    common_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=3600",
        "Content-Type": media_type,
    }

    if range_header:
        m = _RANGE_RE.match(range_header.strip())
        if not m:
            raise HTTPException(status_code=416, detail="invalid range")
        start_s, end_s = m.group(1), m.group(2)
        if start_s == "" and end_s == "":
            raise HTTPException(status_code=416, detail="invalid range")
        if start_s == "":
            suffix = int(end_s)
            if suffix <= 0:
                raise HTTPException(status_code=416, detail="invalid range")
            start = max(0, file_size - suffix)
            end = file_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1

        if start >= file_size or end >= file_size or start > end:
            headers = {"Content-Range": f"bytes */{file_size}"}
            return Response(status_code=416, headers=headers)

        length = end - start + 1

        def _iter_range(path: Path, offset: int, remaining: int, chunk: int = 64 * 1024):
            with open(path, "rb") as f:
                f.seek(offset)
                while remaining > 0:
                    data = f.read(min(chunk, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            **common_headers,
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(length),
        }
        return StreamingResponse(
            _iter_range(audio_file, start, length),
            status_code=206,
            headers=headers,
            media_type=media_type,
        )

    def _iter_full(path: Path, chunk: int = 64 * 1024):
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk)
                if not data:
                    break
                yield data

    headers = {
        **common_headers,
        "Content-Length": str(file_size),
    }
    return StreamingResponse(
        _iter_full(audio_file),
        status_code=200,
        headers=headers,
        media_type=media_type,
    )


@router.get("/api/stream/spotify/{spotify_id}")
def stream_track_by_spotify(spotify_id: str, request: Request, sess: dict = Depends(require_user_stream)):
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT audio_path FROM tracks WHERE spotify_id = ?",
            (spotify_id,),
        ).fetchone()
    finally:
        conn.close()
    return _stream_audio_row(row, request)


@router.get("/api/stream/{track_key}")
def stream_track(track_key: str, request: Request, sess: dict = Depends(require_user_stream)):
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT audio_path FROM tracks WHERE spotify_id = ?",
            (track_key,),
        ).fetchone()
    finally:
        conn.close()
    return _stream_audio_row(row, request)
