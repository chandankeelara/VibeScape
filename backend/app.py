import glob
import os
import shutil
import socket
import tempfile
import time as _time
from pathlib import Path
from typing import Optional

# First: loads .env, imports backend/db.py, then puts the repo root and
# ingest/ on sys.path, in that order (see its docstring).
import bootstrap
from bootstrap import AUDIO_DIR, log

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response

# Through the module, never `from db import get_conn`: db.get_conn is the one
# seam tests patch (conftest.use_conn), and a copied name would not see it.
import db
from routers import admin as admin_routes
from routers import auth as auth_routes
from routers import dj as dj_routes
from routers import events as events_routes
from routers import ingest as ingest_routes
from routers import me as me_routes
from routers import meta as meta_routes
from routers import register as register_routes
from routers import spotify as spotify_routes
from routers import stream as stream_routes
from routers import tracks as tracks_routes
from routers import youtube as youtube_routes

# `features` pulls in librosa/numpy — heavy deps not needed in the
# playback-only prod deployment. Import lazily at call sites that need it
# (only the ingest hot path, and only when Modal ML backend is unavailable).
feat = None

app = FastAPI(title="VibeScape API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers first, then the routes app.py still defines, then the SPA
# catch-all at the bottom, which must stay last or it swallows every API
# route. register(), not app.include_router: see routers/__init__.py.
register_routes(app, auth_routes.router)
register_routes(app, meta_routes.router)
register_routes(app, tracks_routes.router)
register_routes(app, spotify_routes.router)
register_routes(app, admin_routes.router)
register_routes(app, youtube_routes.router)
register_routes(app, stream_routes.router)
register_routes(app, events_routes.router)
register_routes(app, me_routes.router)
register_routes(app, ingest_routes.router)
register_routes(app, dj_routes.router)


@app.on_event("startup")
def _startup():
    db.ensure_db()
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    _log_lan_bind_info()


def _lan_ip() -> Optional[str]:
    """Best-effort local LAN IP so the operator knows where phones should point."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return None


def _log_lan_bind_info() -> None:
    ip = _lan_ip()
    port = os.environ.get("VIBESCAPE_PORT", "8000")
    log.info("VibeScape backend listening on http://0.0.0.0:%s", port)
    if ip:
        log.info("LAN URL (share with phones on same WiFi): http://%s:%s/", ip, port)


NEXT_DIR = Path(__file__).resolve().parent.parent / "frontend-next" / "dist"

# ---------------------------------------------------------------- routing ---
# The React app (frontend-next) is the only UI. The legacy vanilla app that
# used to live under /legacy has been removed now that React is at parity.
#
# ORDER MATTERS: the catch-all at the bottom must stay last, after every API
# route, or it swallows them.


@app.get("/next", include_in_schema=False)
@app.get("/next/{_path:path}", include_in_schema=False)
def serve_next_alias(_path: str = ""):
    """Historical prefix from the strangler phase, when React lived at /next
    to avoid colliding with the legacy app's root-level files. Kept as a
    redirect so old links and bookmarks still resolve."""
    return RedirectResponse(url="/", status_code=308)


# Cache policy for the built SPA. Vite names everything under /assets/ by
# content hash, so those bytes never change under one URL and may be cached
# for a year. Every other file (index.html, sw.js, manifest.json, icons) keeps
# its name across builds and must be revalidated, or a tab keeps the previous
# build's index.html and asks for chunks the new dist/ no longer has.
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
REVALIDATE_CACHE = "no-cache"


def _not_modified(request: Request, headers) -> bool:
    """True when the client's validators match the file. FileResponse sets
    ETag and Last-Modified but never answers a conditional request itself
    (Starlette 1.6; only StaticFiles does), so without this a no-cache
    revalidation re-downloads index.html every time instead of getting a 304."""
    inm = request.headers.get("if-none-match")
    if inm is not None:
        tags = {t.strip().removeprefix("W/") for t in inm.split(",")}
        return "*" in tags or headers["etag"] in tags
    ims = request.headers.get("if-modified-since")
    return ims is not None and ims == headers["last-modified"]


def _static_file(request: Request, file: Path, cache_control: str) -> Response:
    resp = FileResponse(str(file), headers={"Cache-Control": cache_control},
                        stat_result=os.stat(file))
    if _not_modified(request, resp.headers):
        return Response(status_code=304, headers={
            k: resp.headers[k] for k in ("etag", "last-modified", "cache-control")})
    return resp


@app.get("/{path:path}", include_in_schema=False)
def serve_react(request: Request, path: str = ""):
    """
    Serve the built React SPA.

    A real file (hashed asset, manifest.json, sw.js, icons/*) is served
    directly. A missing file is a 404 when the path is under /assets/ or has
    an extension: answering index.html there (200, text/html) is what turned a
    stale tab's lazy import of a chunk the last deploy deleted into "Failed to
    fetch dynamically imported module" plus a MIME error, which the client
    cannot tell from a real module (2026-10-21). Anything else is a client
    route and falls back to index.html so a hard refresh resolves.

    This is a catch-all and MUST remain the last route registered.
    """
    if not NEXT_DIR.is_dir():
        raise HTTPException(
            status_code=503,
            detail={"error": "frontend_not_built",
                    "hint": "cd frontend-next && npm install && npm run build"},
        )
    candidate = (NEXT_DIR / path).resolve()
    is_asset = path.startswith("assets/")
    # Containment check — never serve outside the dist directory.
    if path and candidate.is_file() and NEXT_DIR.resolve() in candidate.parents:
        return _static_file(request, candidate,
                            IMMUTABLE_CACHE if is_asset else REVALIDATE_CACHE)
    if is_asset or "." in path.rsplit("/", 1)[-1]:
        # no-store: a CDN must not keep this 404 for a name a newer build
        # may yet serve.
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    return _static_file(request, NEXT_DIR / "index.html", REVALIDATE_CACHE)
