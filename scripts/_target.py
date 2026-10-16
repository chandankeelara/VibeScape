"""
Which database an offline script talks to: `--target local|prod`.

    local  (default) the SQLite file db_client opens: VIBESCAPE_DB_PATH,
           else data/vibescape.db. DB_BACKEND is forced to sqlite in this
           process, so a .env that points the dev app at Turso cannot take
           a pipeline run there with it.
    prod   production Turso. Credentials come from the gitignored .env.prod
           at the repo root, which must set all three of DB_BACKEND=turso,
           TURSO_DATABASE_URL and TURSO_AUTH_TOKEN (see .env.example). They
           are loaded into this process only, never through .env: uvicorn
           reads .env too, and the dev app must not be able to reach
           production because a pipeline run needed to.

Used by scripts/run_ingest_v2.py and scripts/language_tags.py. Never prints
the token. Does not read scripts/_load_gcp_secrets.ps1.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PROD = PROJECT_ROOT / ".env.prod"
TARGETS = ("local", "prod")
PROD_KEYS = ("DB_BACKEND", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN")


def add_argument(parser) -> None:
    parser.add_argument(
        "--target", choices=TARGETS, default="local",
        help="local: the SQLite file (VIBESCAPE_DB_PATH, else data/vibescape.db). "
             "prod: production Turso, credentials from .env.prod. Default local.")


def apply(name: str, env_file: Path | None = None) -> str:
    """Point this process at `name`'s database and describe it for the log:
    'target=local db=<path>' or 'target=prod host=<host>'.

    Exits with a message, before anything connects, if prod is not
    configured. Must run before the first get_conn(); the caller has
    backend/ on sys.path. `env_file` defaults to ENV_PROD, read at call
    time so tests can point it away from the real file.
    """
    env_file = Path(env_file) if env_file is not None else ENV_PROD
    if name == "local":
        os.environ["DB_BACKEND"] = "sqlite"
        import db_client
        return f"target=local db={db_client._resolve_db_path()}"
    if name != "prod":
        raise SystemExit(f"unknown --target {name!r}; expected one of {', '.join(TARGETS)}")

    if not env_file.is_file():
        raise SystemExit(
            f"--target prod reads {env_file}, which does not exist. Create it with "
            f"{', '.join(PROD_KEYS)} (see .env.example).")
    try:
        from dotenv import dotenv_values, load_dotenv
    except ImportError:
        raise SystemExit("--target prod needs python-dotenv (pip install -r requirements.txt)")
    # Checked in the file itself: a key missing there must not be filled in
    # from .env, which may name a different database.
    values = dotenv_values(env_file)
    missing = [k for k in PROD_KEYS if not (values.get(k) or "").strip()]
    if missing:
        raise SystemExit(f"{env_file.name} must set {', '.join(missing)}")
    backend = values["DB_BACKEND"].strip().lower()
    if backend not in ("turso", "libsql"):
        raise SystemExit(f"{env_file.name} sets DB_BACKEND={values['DB_BACKEND']!r}; "
                         "--target prod needs DB_BACKEND=turso")
    load_dotenv(env_file, override=True)
    host = urlparse(values["TURSO_DATABASE_URL"].strip()).hostname or "?"
    return f"target=prod host={host}"
