"""The FastAPI app, assembled: CORS, every router registered in order with
routers.register (the SPA router last), and the startup hook. No route is
defined here; each lives in routers/ (docs/backend-todo.md, "Backend
module map")."""
import os
import socket
from typing import Optional

# First: loads .env, imports backend/db.py, then puts the repo root and
# ingest/ on sys.path, in that order (see its docstring).
import bootstrap
from bootstrap import AUDIO_DIR, log

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
from routers import spa as spa_routes
from routers import spotify as spotify_routes
from routers import stream as stream_routes
from routers import tracks as tracks_routes
from routers import youtube as youtube_routes

app = FastAPI(title="VibeScape API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In this order, the SPA router last: its catch-all would swallow every
# route registered after it. register(), not app.include_router: see
# routers/__init__.py.
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
register_routes(app, spa_routes.router)  # last: its catch-all


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
