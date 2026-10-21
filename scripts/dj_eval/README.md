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
| `scenarios.json`, `score_dj.py`, `scoreboard.json` | the vibe tests and their listener prompts; one score per DJ version; the committed scoreboard (below) |
| `replay_bench.py --final3` | the skip-overshoot table (2026-10-20; `--final2` is its first version): the old DJ, revision 00028, the candidates; also prints the five regression sessions (`REGRESSION`, scored on the persona's language, film BGM on instrumentals) and the skip-heavy scenarios (mood3, wander6, moved, seedoff3-5, film3 / drone3). A config that wins real-user AUC but fails these is not a candidate: that gap is how the overshoot shipped |
| `replay_bench.py --final4` | the same-song filter (2026-10-20) on PR #23, with `repeat@10`: the share of moments whose top 10 holds another release of a session song or two releases of one song |
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

One number per DJ version, from LLM listeners on three fixed vibe tests
(`scenarios.json`, version 2: Kannada melody, Hindi romantic, English
workout). The DJ knows only what the listener does (searches, plays,
finishes, skips), so a test tells it what is wanted by playing one start song
in that vibe; none asks it to guess. Version 1 also started listeners on a
song in the *wrong* vibe and scored how fast the DJ left it, which penalised
it for doing what it should: continue the one song it was shown.

Each test: search and finish the start song, then exactly 15 DJ picks, each
finished or skipped and rated fit 1-5 against the vibe. Per run:

| metric | | weight |
|---|---|---|
| hold | share of the 15 with fit >= 4 | 50 |
| 1 - skip_rate | share not skipped | 25 |
| skip_recovery | of the picks right after a skipped pick, the share finished | 15 |
| early | share of fit >= 4 in picks 1-3 | 10 |

A run with no pick after a skip has no skip_recovery: its 15 is left out and
the other weights scaled to 100, so it neither pays nor fines. The weights
are `VIBE_WEIGHTS` in `score_dj.py`.

**Steer tests come later**: a listener who changes vibe partway through and
says so only by searching a song in the new vibe (how fast the DJ follows,
whether the old vibe leaks back).

A scenario is the mean of its runs; the DJ score is the mean over scenarios.
**n is small and an LLM listener is noisy**: run every scenario at least
twice. An entry with a single run anywhere is recorded but **provisional** and
cannot become the best, and every comparison says whether the gap is inside
the run-to-run spread (the range of the overall score across the rounds'
first runs, second runs, ...). A gap inside it is not a result.

The start songs, checked read-only on `data/dj_eval/eval.db` on 2026-10-19
(analysed, a 841-float fused_v2 vector, owned by every listener account, and
the query finds it among the listener's 10 search results):

| scenario | account | query | catalogue title, artist | spotify id |
|---|---|---|---|---|
| vibe-kn-melody | kannada-late-night | Anisuthide | Anisuthide, Sonu Nigam | 2Rts79IsoUBDH2fam6LROy |
| vibe-hi-romantic | hindi-romantic | Tum Hi Ho | Tum Hi Ho (From "Aashiqui 2"), Arijit Singh | 7ouBSPZKQpm7zQz2leJXta |
| vibe-en-workout | english-workout | Lose Yourself | Lose Yourself, Eminem | 6GkTKjv1XbFVdI2D8vvDPu |

"Tum Hi Ho" also finds "Tum Hi Ho Bandhu" (Pritam), listed first; the prompt
names the result by title and artist, and a run that played another song than
the scenario's is excluded.

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
   Every `account` in `scenarios.json` must be a persona `setup_copy.py` made
   a user for (it makes one per `personas.json` entry).

2. **The server**, from the code under test, in the foreground (Ctrl-C stops it):

   ```
   python <code>/scripts/dj_eval/serve.py --db <main>/data/dj_eval/eval.db
   ```

3. **The listeners.** For each scenario in `scenarios.json`, launch one
   `dj-listener` agent whose prompt is that scenario's `prompt`, verbatim.
   Launch them **one at a time** (parallel spawns hit a lock). The scenarios
   use different accounts, which is required: two sessions on one account
   reset each other's play history. So a second run of a scenario waits until
   its first has finished. Note each agent's session id from its final
   message.

4. **Score**, from `<main>` (the session files are there; or pass
   `--listener-dir`):

   ```
   python <main>/scripts/dj_eval/score_dj.py --label <name> --commit <sha of <code>> [--prod-revision <rev>] \
       --session vibe-kn-melody=<session id> --session vibe-kn-melody=<second run> ...
   ```

   or `--manifest round.json` with `{"label", "commit", "sessions": {scenario:
   [session ids]}}`. A run's plays must be exactly: the search play of the
   scenario's song (finished), then 15 DJ plays, all rated. Plays past the
   last expected one (agents overshoot) are ignored with a warning. Anything
   else (an up-next play, a second search, too few plays, another start song,
   the wrong account) is printed as EXCLUDED and left out; rerun it. The table
   goes to stdout and an entry to
   `scoreboard.json` (the same label twice is refused; `--replace` overwrites;
   `--dry-run` writes nothing). Commit the scoreboard change. Writing it in
   `<main>` dirties the main checkout: carry the change to a branch.

5. **Stop the server** (Ctrl-C in its terminal).

Changing a scenario, a pick count, a vibe's words or the prompt template: edit
`scenarios.json`, bump its `version` (the best is per version), and run
`score_dj.py --render-prompts`; a test fails while a stored prompt and the
template disagree.
