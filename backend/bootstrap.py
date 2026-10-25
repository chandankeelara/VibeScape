"""Process setup for the backend, run once on first import: .env, sys.path,
the `vibescape` logger, the project paths and the Spotify app config.

The order below is load-bearing:

  1. .env is loaded before anything reads the environment (config.py,
     ADMIN_USER_ID, VIBESCAPE_ML_MODE, DB_BACKEND).
  2. backend/db.py is imported BEFORE ingest/ goes on sys.path. ingest/ has
     its own db.py; with ingest/ first on the path, `import db` anywhere
     afterwards would get that one instead.
  3. Only then do the repo root (config.py) and ingest/ (scoring,
     spotify_*) go on sys.path.

Names tests patch (app_config, PROJECT_ROOT) are read as `bootstrap.<name>`
at call time, never copied with `from bootstrap import ...`, so a patch here
is seen everywhere.
"""
import logging
import sys
from pathlib import Path

# No-op where python-dotenv isn't installed or no .env exists.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import db  # noqa: E402,F401  — must precede the ingest/ path insert (see above)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ingest"))
try:
    import config as app_config
except Exception:
    app_config = None

log = logging.getLogger("vibescape")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

AUDIO_DIR = PROJECT_ROOT / "data" / "audio"


def spotify_creds() -> tuple:
    """(client_id, client_secret, redirect_uri) from config.py, each "" when
    config.py failed to import or lacks the attribute. A value config.py set
    to None comes back as None, as the inline reads it replaced did; every
    caller only tests truthiness or applies `or ""`."""
    cfg = app_config
    if not cfg:
        return "", "", ""
    return (getattr(cfg, "SPOTIFY_CLIENT_ID", ""),
            getattr(cfg, "SPOTIFY_CLIENT_SECRET", ""),
            getattr(cfg, "SPOTIFY_REDIRECT_URI", ""))
