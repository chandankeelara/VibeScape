"""
Run the app on a DJ-eval copy in the foreground until Ctrl-C, for the
listener CLI (listener.py) or anything else that wants a server to talk to.

    python scripts/dj_eval/serve.py --db data/dj_eval/eval.db --port 8765

Same environment rules as server.LocalServer: SQLite only, the copy chosen
with VIBESCAPE_DB_PATH, no --reload.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/

from dj_eval.server import LocalServer  # noqa: E402

DEFAULT_PORT = 8765


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)
    srv = LocalServer(args.db, port=args.port).start()
    print(f"serving {args.db} at {srv.base_url} (pid {srv.proc.pid}); Ctrl-C stops it", flush=True)
    try:
        while srv.proc.poll() is None:
            time.sleep(1)
        print(f"uvicorn exited with {srv.proc.returncode}; see {srv.log_path}")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        srv.stop()


if __name__ == "__main__":
    sys.exit(main())
