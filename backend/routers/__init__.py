"""HTTP routes, one router per area, split out of backend/app.py.

Each module defines `router = Router()` and decorates its endpoints with
`@router.get(...)` exactly as they were decorated with `@app.get(...)`.
app.py registers them with `register(app, router)`, before the routes it
still defines itself, and the SPA catch-all last. Routers import their auth
dependencies from deps.py and never import app.py.

Why not app.include_router: in FastAPI 0.141.1 (the version installed here,
read in fastapi/routing.py on 2026-10-25) include_router does not copy the
routes onto the app, as older releases did with add_api_route. It appends
one `_IncludedRouter` entry to app.routes that resolves the router's routes
at request time. requirements.txt and the Dockerfile ask for
fastapi>=0.104.0, so production gets whichever behaviour is current at build. That entry has no `methods`, `path` or
`dependant`, so tests/backend/test_route_table.py, which pins every route
by reading app.routes, cannot see what it guards. Copying the router's
APIRoute objects into app.routes instead would lose app.dependency_overrides:
a route's handler is built when the route is constructed, with the
dependency_overrides_provider of the router that built it (None for a bare
APIRouter, the app for the app's own router).

So a Router records each add_api_route call, which is where every
@router.get/post/put/delete ends up with all its arguments, and register()
replays that call on app.router.add_api_route. That is the path @app.get
itself takes (FastAPI.get -> app.router.get -> api_route ->
add_api_route), so the app gets the same APIRoute it got before the split,
on any FastAPI version. A Router is still a working APIRouter: switching to
include_router later is a change to register() and the route-table test.
"""
from fastapi import APIRouter


class Router(APIRouter):
    """An APIRouter that remembers its add_api_route calls for register()."""

    def __init__(self) -> None:
        super().__init__()
        self.registrations: list = []

    def add_api_route(self, path, endpoint, **kwargs) -> None:
        self.registrations.append((path, endpoint, kwargs))
        super().add_api_route(path, endpoint, **kwargs)


def register(app, router: Router) -> None:
    """Add every route of `router` to `app`, in the order they were
    declared, as if each had been decorated with @app.<method>."""
    for path, endpoint, kwargs in router.registrations:
        app.router.add_api_route(path, endpoint, **kwargs)
