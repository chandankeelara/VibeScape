"""
Make a working copy of a production backup for the DJ evaluation, with test
users who own every analysed track.

    python scripts/dj_eval/setup_copy.py --backup data/backups/prod_X.db
    python scripts/dj_eval/setup_copy.py --backup ... --out data/dj_eval/eval.db --force

The backup is copied as a file (shutil.copyfile) and never opened, not even
read-only: everything below happens to the copy. The copy and its sidecar
files live under data/dj_eval/, which is gitignored.

WHAT THE COPY GAINS
-------------------
  users        --users eval users ('dj-eval-1'...), one per concurrent
               run_eval.py worker, and one listener user per persona in
               personas.json ('dj-listener-<persona>'), so neither the eval
               workers nor ten concurrent listener agents share play history
               (user_track_stats, which the recency re-rank reads). Not
               'Guest': the recency re-rank is off for that name
               (_DJ_RECENCY_SKIP_GUEST in backend/services/recency.py), and the eval must
               see what a signed-in listener sees.
  user_tracks  every track with ingestion_status='done' and a fused
               embedding, source 'dj_eval'. That is exactly the candidate
               set _similar_dj ranks for a user, so the library and the
               "analysed library" are the same set and the user's
               embedding mean is the mean over all of it.

AUTH
----
Each user gets an email and a random password, hashed in the format
backend/routers/auth.py _hash_password writes (scrypt$salt$hash, n=2**14, r=8, p=1).
No session is minted here: the harness signs in through POST /api/auth/login
on the running server, as the frontend's EmailForm does, and sends the
returned session_token as `Authorization: Bearer` (client.login). The
credentials go to <out>.session.json, next to the copy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import shutil
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
BACKUPS = _REPO / "data" / "backups"
DEFAULT_OUT = _REPO / "data" / "dj_eval" / "eval.db"
PERSONAS = Path(__file__).resolve().with_name("personas.json")
EVAL_USER = "dj-eval"
LISTENER_USER = "dj-listener"
EMAIL_DOMAIN = "dj-eval.local"
DEFAULT_USERS = 4

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def session_file(db_path: Path) -> Path:
    return Path(db_path).with_name(Path(db_path).name + ".session.json")


def load_meta(db_path: Path) -> dict:
    return json.loads(session_file(db_path).read_text(encoding="utf-8"))


def make_copy(backup: Path, out: Path) -> None:
    backup, out = backup.resolve(), out.resolve()
    if out == backup or BACKUPS.resolve() in out.parents:
        raise SystemExit("refusing to write into data/backups: the copy must live elsewhere")
    for side in ("-wal", "-journal"):
        if Path(str(backup) + side).exists():
            raise SystemExit(f"{backup}{side} exists: the backup is mid-write, copy it later")
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(backup, out)


def _hash_password(password: str) -> str:
    # backend/routers/auth.py _hash_password, so /api/auth/login verifies it.
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + salt.hex() + "$" + dk.hex()


def add_user(conn, name: str) -> dict:
    email = f"{name}@{EMAIL_DOMAIN}"
    password = secrets.token_urlsafe(18)
    cur = conn.execute(
        "INSERT INTO users (display_name, email, password_hash) VALUES (?, ?, ?)",
        (name, email, _hash_password(password)))
    user_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO user_tracks (user_id, track_id, source) "
        "SELECT ?, t.id, 'dj_eval' FROM tracks t "
        "JOIN track_embeddings te ON te.track_id = t.id "
        "WHERE t.ingestion_status = 'done' AND te.fused_embedding IS NOT NULL",
        (user_id,))
    n = conn.execute("SELECT COUNT(*) FROM user_tracks WHERE user_id = ?",
                     (user_id,)).fetchone()[0]
    return {"user_id": user_id, "display_name": name, "email": email,
            "password": password, "library": int(n)}


_RESET_TABLES = ("track_events", "user_events", "user_track_stats", "user_stats")


def reset_user(db_path: Path, user_id: int) -> None:
    """Forget a test user's play history, on a copy only."""
    db_path = Path(db_path).resolve()
    if BACKUPS.resolve() in db_path.parents:
        raise SystemExit("refusing to write into data/backups")
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        for t in _RESET_TABLES:
            conn.execute(f"DELETE FROM {t} WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def persona_names(path: Path = PERSONAS) -> list[str]:
    return [p["name"] for p in json.loads(path.read_text(encoding="utf-8"))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backup", type=Path, required=True,
                    help="data/backups/prod_<stamp>.db to copy (never opened)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--users", type=int, default=DEFAULT_USERS,
                    help="eval users, one per concurrent run_eval.py worker (default %(default)s)")
    ap.add_argument("--force", action="store_true", help="replace an existing copy")
    args = ap.parse_args(argv)

    out = args.out
    if out.exists():
        if not args.force:
            print(f"{out} exists; pass --force to replace it")
            return 1
        for p in (out, Path(str(out) + "-wal"), Path(str(out) + "-shm"), session_file(out)):
            p.unlink(missing_ok=True)

    print(f"copy {args.backup} -> {out}")
    make_copy(args.backup, out)
    conn = sqlite3.connect(str(out))
    try:
        users = [add_user(conn, f"{EVAL_USER}-{i + 1}") for i in range(max(1, args.users))]
        listeners = {p: add_user(conn, f"{LISTENER_USER}-{p}") for p in persona_names()}
        conn.commit()
    finally:
        conn.close()
    meta = {"db": str(out.resolve()), "backup": str(args.backup.resolve()),
            "users": users, "listeners": listeners}
    session_file(out).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"{len(users)} eval users, {len(listeners)} listener users, "
          f"{users[0]['library']} tracks each; credentials in {session_file(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
