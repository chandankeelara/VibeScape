# DJ eval

Tests whether the DJ keeps a listener's vibe, through the real
`POST /api/tracks/{key}/similar`, sent the way the frontend sends it, against a
local server on a **copy** of a production backup. Nothing here touches
production or the backup file itself. Everything it writes (the copy,
credentials, listener state, reports) lives under `data/dj_eval/`, which is
gitignored.

Use `D:\Softwares\MiniConda\python.exe` (the PATH python lacks librosa).

```
# 1. the copy: a file copy of the backup, plus 4 eval users and one user per persona
python scripts/dj_eval/setup_copy.py --backup data/backups/prod_<stamp>.db      # -> data/dj_eval/eval.db

# 2a. the deterministic eval: starts and stops its own server (~1 h for every group)
python scripts/dj_eval/run_eval.py --db data/dj_eval/eval.db                     # -> data/dj_eval/reports/dj_eval_*.md/.json

# 2b. listener agents: a server in the foreground on :8765 (no --reload), Ctrl-C stops it
python scripts/dj_eval/serve.py --db data/dj_eval/eval.db
#     then launch one dj-listener agent per persona in personas.json, and:
python scripts/dj_eval/report.py                                                 # -> data/dj_eval/reports/listeners_*.md/.json
```

| file | what |
|---|---|
| `setup_copy.py` | the copy and its users; credentials in `eval.db.session.json` |
| `server.py`, `serve.py` | uvicorn on the copy, SQLite forced, every `TURSO_*` dropped |
| `client.py` | login, the DJ request, the event log, `/api/events` telemetry, as the frontend does them |
| `groups.py`, `run_eval.py` | groups from the data (language, language x energy tercile, album / film, primary artist) and the four scenarios |
| `listener.py` | the CLI a listener agent drives; prints only handle, title, artist, album |
| `personas.json` | the ten personas |
| `report.py` | per-persona transcripts, acceptance by DJ step, reviews, side by side |
| `dj-listener.agent.md` | the agent definition |

**Install the agent** (`.claude/agents/` is gitignored, so a clone has none):
copy `dj-listener.agent.md` to `.claude/agents/dj-listener.md` and replace
`<repo>` with this checkout's absolute path, e.g.
`sed "s#<repo>#$PWD#" scripts/dj_eval/dj-listener.agent.md > .claude/agents/dj-listener.md`.
Its only tool is Bash and it is told to run nothing but `listener.py`; that is
an instruction, not a sandbox.

Auth is the app's own: `setup_copy.py` gives each user an email and a scrypt
password, and the harness signs in with `POST /api/auth/login` and sends
`Authorization: Bearer <session_token>`. A DJ request writes two things on the
copy: `sessions.last_used_at` (every authenticated request does) and, on a
user's first request, the `user_embedding_mean` cache row. Plays go to
`/api/events`, so they reach `user_track_stats` and the recency re-rank; the
eval and `listener.py start` wipe the test user's history first.

The copy runs on SQLite, so the DJ ranks with the numpy path, not the
`vector_distance_cos` SQL production uses on Turso.
