"""routers.register(): why app.py does not use app.include_router.

A Router's routes must land in app.routes as the app's own APIRoutes, so
test_route_table.py can read their methods, path and auth (FastAPI 0.141's
include_router appends one opaque _IncludedRouter entry instead), and
app.dependency_overrides must reach them, which copying the router's own
route objects would not do. See backend/routers/__init__.py.
"""
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from routers import Router, register


def who():
    return "real"


def _app():
    router = Router()

    @router.get("/a")
    def a(user: str = Depends(who)):
        return {"user": user}

    @router.post("/b", status_code=202)
    def b():
        return {}

    app = FastAPI()
    register(app, router)
    return app


def test_registered_routes_are_the_apps_own_api_routes_in_order():
    app = _app()
    mine = [r for r in app.routes if getattr(r, "path", None) in ("/a", "/b")]
    assert [(r.path, sorted(r.methods)) for r in mine] == [("/a", ["GET"]), ("/b", ["POST"])]
    assert all(type(r) is APIRoute for r in mine)
    assert [r for r in app.routes if r.path == "/b"][0].status_code == 202


def test_app_dependency_overrides_reach_registered_routes():
    app = _app()
    client = TestClient(app)
    assert client.get("/a").json() == {"user": "real"}
    app.dependency_overrides[who] = lambda: "override"
    assert client.get("/a").json() == {"user": "override"}
