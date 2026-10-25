"""Backend services: logic shared by the routes in app.py (and, later in
the refactor, by routers/), with no FastAPI route of its own.

backend/ is the import root (uvicorn runs `app:app` from it), so these are
imported as `services.<name>`. A name a test patches is read as a module
attribute at call time, never copied with `from services.x import name`.
"""
