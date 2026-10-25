"""Backend services: logic the routes in routers/ call, with no FastAPI
route of its own.

backend/ is the import root (uvicorn runs `app:app` from it), so these are
imported as `services.<name>`. A name a test patches is read as a module
attribute at call time, never copied with `from services.x import name`.
"""
