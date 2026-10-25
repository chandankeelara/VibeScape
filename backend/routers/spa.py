"""The built React SPA (frontend-next/dist): the /next redirects left from
the strangler phase, and the catch-all that serves hashed assets, the other
dist files and index.html for client routes.

Moved out of app.py unchanged (refactor PR 5, 2026-10-25), except that
NEXT_DIR climbs one directory more, since this file sits in backend/routers/
rather than backend/: it is still <repo>/frontend-next/dist. Tests patch
NEXT_DIR (test_spa_serving), so serve_react reads it as a module global at
call time; nothing copies it. app.py registers this router LAST: the
catch-all would otherwise answer every API path registered after it.
"""
import os
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from routers import Router

router = Router()


NEXT_DIR = Path(__file__).resolve().parent.parent.parent / "frontend-next" / "dist"

# ---------------------------------------------------------------- routing ---
# The React app (frontend-next) is the only UI. The legacy vanilla app that
# used to live under /legacy has been removed now that React is at parity.
#
# ORDER MATTERS: the catch-all at the bottom must stay last, after every API
# route, or it swallows them.


@router.get("/next", include_in_schema=False)
@router.get("/next/{_path:path}", include_in_schema=False)
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


@router.get("/{path:path}", include_in_schema=False)
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
