"""The route table, pinned. backend/app.py is being split into routers over
several PRs (docs/backend-todo.md, "Refactor in progress"); each move must
leave every (methods, path) where it was, with the same auth dependency, and
the SPA catch-all last. A route that silently drops out, changes method or
loses its auth fails here before any behaviour test would notice.

Auth is read from the route's dependency tree by function NAME, not
identity, so the guard survives require_user moving to another module.
"""

NONE, USER, ADMIN, STREAM = "none", "require_user", "require_admin", "require_user_stream"

# (methods, path, auth), in registration order. Captured from main at 535efff.
EXPECTED = [
    ({"GET", "HEAD"}, "/openapi.json", NONE),
    ({"GET", "HEAD"}, "/docs", NONE),
    ({"GET", "HEAD"}, "/docs/oauth2-redirect", NONE),
    ({"GET", "HEAD"}, "/redoc", NONE),
    ({"POST"}, "/api/auth/signup", NONE),
    ({"POST"}, "/api/auth/login", NONE),
    ({"POST"}, "/api/auth/logout", USER),
    ({"GET"}, "/api/auth/me", USER),
    ({"POST"}, "/api/auth/spotify-oauth", NONE),
    ({"POST"}, "/api/spotify/refresh", USER),
    ({"GET"}, "/api/demo/moods", NONE),
    ({"POST"}, "/api/auth/guest", NONE),
    ({"POST"}, "/api/auth/spotify-link", USER),
    ({"GET"}, "/api/health", NONE),
    ({"GET"}, "/api/moods", NONE),
    ({"GET"}, "/api/tracks", USER),
    ({"GET"}, "/api/tracks/search", USER),
    ({"GET"}, "/api/tracks/{track_key}/similar", USER),
    ({"POST"}, "/api/tracks/{track_key}/similar", USER),
    ({"GET"}, "/api/tracks/random", USER),
    ({"GET"}, "/api/spotify/status", NONE),
    ({"GET"}, "/api/spotify/config", NONE),
    ({"GET"}, "/api/client-config", NONE),
    ({"GET"}, "/api/track/{apple_id}/spotify", USER),
    ({"GET"}, "/callback", NONE),
    ({"GET"}, "/api/stream/spotify/{spotify_id}", STREAM),
    ({"GET"}, "/api/stream/{track_key}", STREAM),
    ({"POST"}, "/api/recompute-scores", ADMIN),
    ({"GET"}, "/api/tracks/{track_key}/features", USER),
    ({"GET"}, "/api/tracks/{track_key}/lyrics", USER),
    ({"GET"}, "/api/tracks/{track_id}/youtube", USER),
    ({"GET"}, "/api/tracks/{track_id}/youtube/search", USER),
    ({"POST"}, "/api/tracks/{track_id}/youtube", ADMIN),
    ({"GET"}, "/api/spotify/library", USER),
    ({"GET"}, "/api/spotify/search", USER),
    ({"POST"}, "/api/ingest/clear", USER),
    ({"PUT"}, "/api/me/library/{track_key}", USER),
    ({"DELETE"}, "/api/me/library/{track_key}", USER),
    ({"POST"}, "/api/ingest/spotify", USER),
    ({"POST"}, "/api/ingest/single", USER),
    ({"POST"}, "/api/ingest/spotify-public", USER),
    ({"POST"}, "/api/ingest/playlist-url", USER),
    ({"GET"}, "/api/ingest/status/{job_id}", USER),
    ({"DELETE"}, "/api/ingest/status/{job_id}", USER),
    ({"POST"}, "/api/events", USER),
    ({"GET"}, "/api/me/stats", USER),
    ({"GET"}, "/api/admin/users", ADMIN),
    ({"GET"}, "/api/admin/users/{user_id}/stats", ADMIN),
    ({"GET"}, "/api/admin/users/{user_id}/tracks", ADMIN),
    ({"DELETE"}, "/api/admin/users/{user_id}", ADMIN),
    ({"GET"}, "/next/{_path:path}", NONE),
    ({"GET"}, "/next", NONE),
    ({"GET"}, "/{path:path}", NONE),
]


def _dep_names(dependant):
    names = []
    for sub in dependant.dependencies:
        names.append(getattr(sub.call, "__name__", ""))
        names.extend(_dep_names(sub))
    return names


def _auth(route):
    dependant = getattr(route, "dependant", None)
    names = set(_dep_names(dependant)) if dependant else set()
    for level in (ADMIN, STREAM, USER):        # admin implies require_user beneath it
        if level in names:
            return level
    return NONE


def _table(app_module):
    return [(set(r.methods or ()), r.path, _auth(r)) for r in app_module.app.routes]


def _key(row):
    methods, path, auth = row
    return (tuple(sorted(methods)), path, auth)


def test_every_route_with_its_methods_and_auth(app_module):
    actual = sorted(map(_key, _table(app_module)))
    expected = sorted(map(_key, EXPECTED))
    missing = [r for r in expected if r not in actual]
    extra = [r for r in actual if r not in expected]
    assert not missing and not extra, f"missing={missing}\nextra={extra}"


def test_no_route_is_registered_twice(app_module):
    keys = [(m, p) for m, p, _ in map(_key, _table(app_module))]
    assert len(keys) == len(set(keys))


def test_the_spa_catch_all_is_last_and_every_api_route_precedes_it(app_module):
    paths = [r.path for r in app_module.app.routes]
    assert paths[-1] == "/{path:path}"
    assert paths.count("/{path:path}") == 1
    # /next redirects; the catch-all would serve index.html for it instead.
    assert paths.index("/next") < paths.index("/{path:path}")
    assert paths.index("/next/{_path:path}") < paths.index("/{path:path}")
    assert all(paths.index(p) < len(paths) - 1 for p in paths if p.startswith("/api/"))


def test_admin_routes_are_admin_and_stream_routes_take_the_query_token(app_module):
    table = {(tuple(sorted(m)), p): a for m, p, a in _table(app_module)}
    for (_, path), auth in table.items():
        if path.startswith("/api/admin/"):
            assert auth == ADMIN, path
        if path.startswith("/api/stream/"):
            assert auth == STREAM, path
        elif auth == STREAM:
            raise AssertionError(f"{path}: ?token= auth is for /api/stream/* only")
