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

# 1b. the copy is fused_v1 (as prod is until the migration): migrate it
VIBESCAPE_DB_PATH=data/dj_eval/eval.db python scripts/refuse_vectors.py --apply

# 2a. the deterministic eval: starts and stops its own server (~1 h for every group)
python scripts/dj_eval/run_eval.py --db data/dj_eval/eval.db                     # -> data/dj_eval/reports/dj_eval_*.md/.json

# 2a'. the replay benchmark: no server, deterministic, ~1-2 min per vector space
python scripts/dj_eval/replay_bench.py --db data/dj_eval/eval.db --listeners data/dj_eval/listener   # -> reports/replay_bench_*.json

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
| `replay_bench.py` | replays the recorded listener sessions and the real users' `track_events` through `dj_replay`, in vector spaces it rebuilds in memory; finished-vs-skipped AUC, language match, duplicates, the 'single' scenario's group precision offline, and synthetic scenarios (a 70/30 library, skips, a language switch, mixed skips, another release of a skipped track). `--final` is the PR #21 table |
| `catch_hold.json`, `score_dj.py`, `scoreboard.json` | the catch-and-hold scenarios and their listener prompts; one score per DJ version; the committed scoreboard (below) |
| `language_rerank.py` | the session language re-rank that was measured against the vector-only DJ and not shipped; kept so the comparison reproduces |
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

The replay benchmark ranks with the same pure functions the endpoint calls
(`backend/dj_replay.py`) and the endpoint's pool
size, so it judges a change without the server or an LLM. On the baseline it
reproduces run_eval.py's language precision to within 0.01 and ranks every
DJ-served listener track inside its top 8 (the `top8` column). It does not
run the recency re-rank: `user_track_stats` holds the final state, which
would leak the future into the replay.

## Scoring a DJ version

One number per DJ version, from LLM listeners on six fixed scenarios
(`catch_hold.json`): a listener plays one fixed start song from a search, then
lets the DJ pick exactly 15 songs, finishing or skipping each and rating its
fit 1-5 against a target that is the same for a scenario's `on` and `off`
runs. `on` starts from a song that fits the target (does the DJ keep it?),
`off` from one that does not (how fast does it find the listener?). Per run:

- **catch step**: the first DJ step whose pick and the two after it all have fit >= 4;
- **catch** = 50 x (16 - catch step) / 15 (50 at step 1, 10 at step 13, 0 if never);
- **hold** = 50 x the share of picks from the catch step on with fit >= 4 (0 if never caught);
- **lost**: picks after the catch with fit <= 2, reported, not scored.

A scenario is the mean of its runs; `on`, `off` and the overall DJ score are
means over scenarios. **n is small and an LLM listener is noisy**: run every
scenario at least twice. An entry with a single run anywhere is recorded but
**provisional** and cannot become the best, and every comparison says whether
the gap is inside the run-to-run spread (the range of the overall score across
the rounds' first runs, second runs, ...). A gap inside it is not a result.

Paths: `<main>` is the main checkout (the listener agent's `<repo>`, where the
copy and the session files live), `<code>` the worktree of the DJ version
under test. Everything runs with `D:\Softwares\MiniConda\python.exe`.

1. **The copy**, with the code under test's vector recipe:

   ```
   python <main>/scripts/dj_eval/setup_copy.py --backup <main>/data/backups/prod_<stamp>.db --force
   VIBESCAPE_DB_PATH=<main>/data/dj_eval/eval.db python <code>/scripts/refuse_vectors.py --apply
   ```

   `listener.py start` reads the copy's credentials from
   `<main>/data/dj_eval/eval.db.session.json`, so the copy must be that file.
   Every `account` in `catch_hold.json` must be a persona `setup_copy.py` made
   a user for (it makes one per `personas.json` entry).

2. **The server**, from the code under test, in the foreground (Ctrl-C stops it):

   ```
   python <code>/scripts/dj_eval/serve.py --db <main>/data/dj_eval/eval.db
   ```

3. **The listeners.** For each scenario in `catch_hold.json`, launch one
   `dj-listener` agent whose prompt is that scenario's `prompt`, verbatim.
   Launch them **one at a time** (parallel spawns hit a lock), at most about 6
   running at once. The six scenarios use six different accounts, which is
   required: two sessions on one account reset each other's play history. So a
   second run of a scenario waits until its first has finished. Note each
   agent's session id from its final message.

4. **Score**, from `<main>` (the session files are there; or pass
   `--listener-dir`):

   ```
   python <main>/scripts/dj_eval/score_dj.py --label <name> --commit <sha of <code>> [--prod-revision <rev>] \
       --session kn-melody-on=<session id> --session kn-melody-on=<second run> ...
   ```

   or `--manifest round.json` with `{"label", "commit", "sessions": {scenario:
   [session ids]}}`. A session that is not exactly one search play then 15 DJ
   plays, all rated, on the scenario's account, is printed as EXCLUDED and left
   out; rerun it. The table goes to stdout and an entry to `scoreboard.json`
   (the same label twice is refused; `--replace` overwrites; `--dry-run`
   writes nothing). Commit the scoreboard change. Writing it in `<main>` dirties
   the main checkout: carry the change to a branch.

5. **Stop the server** (Ctrl-C in its terminal).

Changing a scenario, the step count or the prompt template: edit
`catch_hold.json`, bump its `version` (the best is per version), and run
`score_dj.py --render-prompts`; a test fails while a stored prompt and the
template disagree.
