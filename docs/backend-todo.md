# Backend backlog

The running backlog for everything the backend owner owns: `backend/`,
`ingest/`, `ingest_pipeline/`, `scripts/`, `deploy/cloud-run/`, `schema.sql`
and the Dockerfile.

**How this file is maintained.** Read it at the start of every backend task
and update it at the end — add what you found, tick what you fixed, delete
what turned out not to be real. It is a backlog, not a diary: an entry nobody
would act on does not belong here.

**Entry format.** Every item carries a `file:line`, a one-sentence statement
of what concretely breaks (inputs or state → wrong result), and a
**verified** / **suspected** tag:

- **verified** — established by reading the code, parsing it with `ast`, git
  archaeology, or querying the local DB. Nothing here was established by
  running the app.
- **suspected** — the mechanism is clear from the code but the symptom has
  not been observed; each one names the cheap check that would settle it.

Items marked **[frontend contract]** cannot be fixed without the frontend
owner knowing: they change a response shape, a parameter name, a status
value, or a timing assumption.

Ranked: broken now → will break → correctness risks → cleanup.

Audit date: 2026-10-02. No automated tests exist anywhere in the repo
(`find . -name 'test_*.py'` → nothing), which is why so much of this is
"nothing ever checked the claim".

---

## 1. Broken now

### 1.1 `/api/recompute-scores` rewrites every track in the database, for every user
`backend/app.py:1901` (`del user_id`), called from `backend/app.py:1959`,
`backend/app.py:3000`, `backend/app.py:3311`

**verified.** `_recompute_axes_and_zscores` takes a `user_id`, discards it on
line 1901, then issues `SELECT … FROM tracks` with no `WHERE` and one
`UPDATE tracks SET activation, valence, activation_relative, vibe_score,
mood WHERE id = ?` per scored row. Any signed-in user POSTing
`/api/recompute-scores` rewrites the mood and vibe score of every track in
the shared catalogue, for everyone. The local DB has 3,783 scored rows; on
Turso each `UPDATE` is a separate HTTPS round trip, so that is ~3,800
sequential requests per call — and the same call runs at the end of *every*
ingest job. Two fixes are needed and they are separable: scope the write, and
batch the round trips.

**[frontend contract]** The docstring at `backend/app.py:1964` states
"Per-user scoped: only touches rows belonging to the caller." That is false
and has been since `del user_id` was added. Anything that calls this
endpoint believing it is cheap and local is wrong on both counts.

### 1.2 `vibe_score` has two writers that disagree on what it means
`ingest_pipeline/stage_classify.py:123` vs `backend/app.py:1941`

**verified.** ClassifyStage writes `vibe_score = activation` (the raw 0–100
axis). `_recompute_axes_and_zscores` writes `vibe_score = rel`, the z-scored
`activation_relative`. Since 1.1 runs after every sync, a freshly classified
track's `vibe_score` means "raw activation" for as long as it takes the next
sync to finish, then silently means "relative to library mean". Any UI or
query that compares `vibe_score` across tracks ingested at different times is
comparing two different quantities. **[frontend contract]** — whichever
definition wins is the one the client has been rendering.

### 1.3 ~~The sync picker still truncates at 200 playlists~~ — DONE (2da72af)
`backend/app.py:2386` vs `ingest/spotify_library.py:181`

**verified.** `get_playlists`'s docstring says the 200 cap "used to" truncate
silently and that the default was raised to 500. The one caller passes
`max_items=200` explicitly, so the fix never reached production: an account
with 300 playlists sees 200 in the sync picker and no indication that the
rest exist. `_paginate` returns on `fetched >= max_items` with no signal, so
nothing downstream can tell truncation from "that's all of them".
**[frontend contract]** — the picker's list gets longer.

### 1.4 ~~Public-playlist ingest never clears `collecting`~~ — DONE (2da72af)
`backend/app.py:3451`

**verified.** `_run_public_playlist_job` seeds the job dict with
`"collecting": True` and never writes `collecting=False` on any path —
success, cancel or error. `_run_ingest_job` does, at `backend/app.py:2987`.
Per the comment at `backend/app.py:3447`, the client reads `collecting` to
decide between "found N so far" and a percentage, so a public-playlist import
sits on the indeterminate text forever even after `status` goes `complete`.
**[frontend contract]** — the field is read by the sync modal.

### 1.5 The ingest job dict is per-process, but Cloud Run runs up to 3 instances with CPU throttling and scale-to-zero
`backend/app.py:2315` (`JOBS`), `deploy/cloud-run/deploy.ps1:35`

**suspected** (mechanism verified from the deploy flags and the code; the
symptom has not been observed). `deploy.ps1` passes
`--min-instances 0 --max-instances 3` and does **not** pass
`--no-cpu-throttling`. Three consequences, all behind HTTP 200/202:

1. `POST /api/ingest/spotify` returns `job_id` from instance A; a later
   `GET /api/ingest/status/{job_id}` load-balanced to instance B finds
   nothing in its own `JOBS` and returns **404 "job not found"**
   mid-sync, non-deterministically.
2. Cloud Run's default CPU allocation throttles the instance to near-zero
   between requests. The ingest worker is a plain `threading.Thread`
   started after the 202 is written, so it only advances while some
   request happens to be in flight.
3. With `min-instances 0` the instance is reclaimed once polling stops;
   the thread dies, rows already written stay, and the job vanishes.

Cheapest check: start a sync against prod and watch for a 404 from the status
endpoint while `status` was `running`. Any real fix moves job state out of
process (a `jobs` table) — **[frontend contract]**, since the status endpoint
gains durability semantics it does not have today.

### 1.6 `FuseStage` cannot run against Turso: it reads the MERT blob raw
`ingest_pipeline/stage_fuse.py:86`

**verified by reading** (not run against Turso). `process_row` does
`blob = row["mert_blob"]` from a plain `SELECT te.mert_embedding`. On the
Hrana read path that decodes to `b''` — which is exactly why
`backend/app.py:1005` and `backend/app.py:1040` go out of their way to wrap
the same columns in `vector_extract()`. `if not blob:` is true for `b''`, so
with `DB_BACKEND=turso` every row returns `STATUS_FAILED` with
"mert_embedding missing", `_finalize_fields` parks it at
`ingestion_status='fuse_stage_error'`, and no fused vector is ever written.
`run_ingest_v2.py` uses `backend.db.get_conn()`, which honours `DB_BACKEND`,
so this is reachable by configuration alone. The fix is the same
`vector_extract()` workaround app.py already uses — or item 2.1 below, which
removes the need for both.

### 1.7 A fresh local SQLite bootstrap dies in `_seed_default_user`: `users` has no `pin_hash`
`backend/db.py:195` vs `schema.sql:6-20`

**verified by running it** (2026-10-02). On a DB with zero `users` rows,
`_migrate` reaches `_seed_default_user`, which does
`INSERT INTO users (display_name, pin_hash) VALUES (?, NULL)`. PINs were
removed from `schema.sql` in the unified-identity refactor, so the column
does not exist and the statement raises
`OperationalError: table users has no column named pin_hash` straight out of
`ensure_db()` — i.e. out of `get_conn()`, i.e. out of every authenticated
request. A developer cloning the repo without `data/vibescape.db` gets a
500 on everything. The existing dev DB still has the column, which is why
nobody has hit it. Drop `pin_hash` from the INSERT.

---

## 2. Will break

### 2.1 The Hrana BLOB read bug is a one-line base64 padding failure — CONFIRMED
`backend/db_client.py:86-90`

**suspected**, high confidence, and worth settling because it is the root of
every `vector_extract()` workaround in the codebase. The decoder is
`base64.b64decode(cell.get("base64") or "")` inside a bare
`except Exception: return b""`. Hrana encodes blob values as **base64url
without padding**; `base64.b64decode` uses the standard alphabet and rejects
missing padding. The result is an exception that the `except` swallows into
`b''` — which matches the observed symptom exactly (empty bytes, never an
error). `_hrana_arg` encodes with padded standard base64 on the write path,
which the server evidently accepts, so only reads are affected.

Check: `SELECT mert_embedding, LENGTH(mert_embedding) FROM track_embeddings
LIMIT 1` against Turso and compare `len(row[0])` to `row[1]`; then retry the
decode as `base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))`. If that is it,
items 1.6 and the `_is_turso()` branches at `backend/app.py:1003` and
`backend/app.py:1040` all collapse into one fix. Whatever the outcome, the
bare `except Exception: return b""` should log — it is the reason this cost a
confident, wrong "production embeddings are empty" report.

### 2.2 Turso has no transactions at all, and `commit()`'s docstring says otherwise
`backend/db_client.py:230`, mechanism at `backend/db_client.py:196-203`

**verified.** Every `execute()` posts `[{"type":"execute"}, {"type":"close"}]`
with `baton: None` — a fresh Hrana stream per statement, closed immediately.
So `BEGIN` opens a transaction on a stream that is torn down before the next
statement runs, and `commit()`'s claim that "explicit BEGIN/COMMIT/ROLLBACK
travels through .execute() already" is false; `rollback()` issues a `ROLLBACK`
against no transaction and swallows the error. Concretely, on Turso:

- `_process_track` (`backend/app.py:2737`, `backend/app.py:2761`) inserts the
  `tracks` row and the `user_tracks` link as two independent commits. A crash
  between them leaves a catalogue row no user references and the user without
  the track — permanently, since `_process_track` short-circuits on
  `SELECT … FROM tracks WHERE spotify_id = ?` on the next attempt.
- `ingest_clear` (`backend/app.py:2563`) deletes `user_tracks` then loops
  deleting `tracks` row by row with no way to roll back a partial run.
- `scripts/_dedupe_turso_by_isrc.py:219-220` deletes a track's embeddings and
  then the track as two separate commits.

Fix is either Hrana batch requests (several statements in one pipeline POST,
which also fixes the round-trip cost in 1.1) or baton reuse. Either way,
correct the docstring first.

### 2.3 Every `except sqlite3.IntegrityError` in the codebase is dead on Turso
`backend/app.py:338`, `ingest_pipeline/base.py:253`, `ingest_pipeline/base.py:264`

**verified.** `db_client` raises exactly two exception types:
`sqlite3.OperationalError` for any Turso-reported error
(`backend/db_client.py:210`, `backend/db_client.py:214`) and
`requests.HTTPError` from `raise_for_status()` at `backend/db_client.py:191`.
It never raises `IntegrityError`. So:

- `backend/app.py:338` — a signup that loses the race past the pre-check
  returns **HTTP 500** instead of 409 `email_taken`. **[frontend contract]**
- `ingest_pipeline/base.py:253` — the whole `_RETRY_DROPPABLE_FIELDS` retry
  for legacy `UNIQUE (user_id, apple_id)` collisions never fires in
  production; the exception escapes `_commit_row_update`.

### 2.4 One bad row discards an entire pipeline batch, including GPU work
`ingest_pipeline/base.py:197-201`, same shape at
`ingest_pipeline/stage_classify.py:160-181` and `ingest_pipeline/stage_fuse.py:130-143`

**verified.** `run_batch` loops `_commit_row_update` over every result and
calls `conn.commit()` *after* the loop. If any row raises (see 2.3 — on Turso
it will), the loop aborts before the commit and local SQLite rolls back every
update already applied in that batch. The rows stay `pending`, so the next
pass re-selects the same cohort, re-runs the same MERT forward passes and
fails on the same row — a loop that burns GPU time indefinitely. Commit
per row, or catch per row.

### 2.5 `--retry-failed` does not exist
`ingest_pipeline/base.py:36` and `ingest_pipeline/base.py:72` vs
`scripts/run_ingest_v2.py:154-166`

**verified.** `base.py` twice tells the reader that a `failed` row can be
resumed by re-running "the orchestrator with `--retry-failed`". The
orchestrator's `argparse` defines `--batch`, `--stages`, `--loop`,
`--interval`, `--verbose` and nothing else. A row that hits an exception is
parked at `ingestion_status='<stage>_stage_error'`, excluded from
`select_cohort` (`scripts/run_ingest_v2.py:108-116`), and there is no
supported way to requeue it. Either build the flag or delete the claim.

### 2.6 `get_conn()` runs the full migration ladder on every single call
`backend/db.py:970`

**verified.** `get_conn()` calls `ensure_db()` unconditionally. On SQLite that
opens a second connection and runs `schema.sql` through `executescript` plus
`_migrate`'s ~50 `ALTER TABLE` statements (each in its own
try/except/commit), plus `_backfill_classification_source`, which does one
`UPDATE` per matching row. `require_user` calls `get_conn()` on every
authenticated request, so every local request pays this. `ensure_db()` returns
immediately on Turso (`backend/db.py:933`), so production is spared — but
local development and `run_ingest_v2.py` are not. Guard it with a
process-level "already ensured" flag.

---

## 3. Correctness risks

### 3.1 A track with `ingestion_status IS NULL` jams the head of the cohort queue
`scripts/run_ingest_v2.py:108-116` vs `ingest_pipeline/stage_preview.py:52-58`

**suspected.** `select_cohort` admits rows where `ingestion_status = 'pending'
**OR ingestion_status IS NULL**`, ordered `id ASC LIMIT batch`. `PreviewStage`
— the only entry stage — matches `ingestion_status = 'pending'` strictly. A
row with NULL `ingestion_status` and NULL stage columns is therefore selected
into every cohort and matched by no stage. Since the cohort is the lowest
`batch` ids, enough such rows (default batch 50) permanently consume the
whole cohort and the pipeline reports `processed=0` while real work sits
behind them. `base.py:16-23` says prod Turso's `tracks` was rebuilt without
column defaults, which is how NULLs get there. Local DB has zero such rows
(checked). Check on prod:
`SELECT COUNT(*) FROM tracks WHERE ingestion_status IS NULL`.

### 3.2 `CORS allow_origins=["*"]` with `allow_credentials=True`, next to a token-in-query-string auth path
`backend/app.py:109-113`, `backend/app.py:263`

**verified** (configuration, not exploitation). Every origin on the web may
call every endpoint. The exposure is bounded because auth is a bearer header
rather than a cookie — except `require_user_stream`, which deliberately
accepts `?token=` for `<audio>` tags. A session token that leaks into a
Referer header, a proxy log or a shared URL is then directly replayable
cross-origin against `/api/stream/*`. Narrow the origin list to the Cloud Run
URL and localhost. **[frontend contract]** if the frontend is ever served
from a second origin.

### 3.3 Sessions never expire and are never pruned
`backend/app.py:202` (`_issue_session`), `backend/app.py:212` (`_lookup_session`)

**verified.** `_issue_session` inserts a token with no expiry;
`_lookup_session` bumps `last_used_at` but checks nothing. Only
`/api/auth/logout` deletes a row. Every token ever issued stays valid forever
and the `sessions` table grows without bound (18 rows locally). Add an expiry
column and a check, plus a prune. **[frontend contract]** — clients need to
handle a 401 on a previously good token.

### 3.4 Unverified claim: Spotify `/v1/search` caps `limit` at 10
`backend/app.py:2470-2472`

**suspected wrong.** The comment asserts "Spotify /v1/search caps `limit` at
10 as of late 2024 … Requesting 11+ returns 400 'Invalid limit'", and
`Query(10, ge=1, le=10)` enforces it, so a client asking for 25 gets a 422
from *us*, not from Spotify. Spotify's documented range for `/v1/search` is
0–50. This is the same shape as the `/me/top/tracks` 50-clamp: a comment that
nothing ever tested, enforced in code. One request with `limit=25` settles it;
whichever way it lands, write the probe and the date into the docstring the
way `get_top_tracks_count` now does. **[frontend contract]** if the cap moves.

### 3.5 `FuseStage` writes the fused vector with a blind `UPDATE`
`ingest_pipeline/stage_fuse.py:137-141`

**verified.** `UPDATE track_embeddings SET fused_embedding = ? WHERE
track_id = ?` affects zero rows if no `track_embeddings` row exists, yet
`fuse_status` is still set to `done` and `youtube_status` is armed — so the
track reaches `ingestion_status='done'` with no fused vector and silently
drops out of the DJ's candidate pool. Today `fetch_pending`
(`ingest_pipeline/stage_fuse.py:58`) `JOIN`s `track_embeddings`, so the row
always exists and this is latent; `ClassifyStage` already uses
`ON CONFLICT … DO UPDATE` for the same table. Make fuse's write an upsert too,
or assert `rowcount == 1`.

### 3.6 Spotify 429 handling caps the backoff at 10 s and then gives up
`ingest/spotify_library.py:110-112`, `ingest/spotify_library.py:320`,
`ingest/spotify_library.py:383`

**verified.** `_get` retries three times; a 429 sleeps
`min(int(Retry-After), 10)`. Spotify's `Retry-After` on a real rate limit is
routinely far longer, so three capped sleeps burn the budget and raise
`SpotifyAPIError("exhausted retries")`, which `_run_ingest_job` turns into
`status="error"` partway through a large library. Separately,
`int(r.headers.get("Retry-After", "1"))` raises an uncaught `ValueError` if
Spotify ever sends an HTTP-date instead of seconds. Honour the full
`Retry-After` (it is the server telling you the answer) and parse defensively.

### 3.7 The SPA catch-all answers unknown `/api/*` GETs with `index.html` and HTTP 200
`backend/app.py:3729`

**verified.** `@app.get("/{path:path}")` is registered last and falls back to
`index.html` for anything that is not a file on disk. A GET to a mistyped or
removed API route returns 200 with HTML, so a client's `.json()` fails with a
parse error rather than a 404 it could handle. Return 404 for paths starting
with `/api/`. **[frontend contract]** — error handling changes shape.

---

## 4. Cleanup

### 4.1 Stale file references
**verified**, all four:

- `backend/app.py:2687` — `_process_track`'s docstring sends the reader to
  `scripts/run_ingest_worker.py`. The file does not exist; the runner is
  `scripts/run_ingest_v2.py`.
- `schema.sql:111` — same dead reference, in the two-phase ingestion comment.
- `backend/db.py:926` — `ensure_db` says the Turso schema is managed by
  `scripts/migrate_to_turso_http.py`. That file does not exist, so the
  comment names no actual owner for the prod schema.
- `ingest_pipeline/base.py:19` — cites `scripts/_push_local_to_turso.py` as
  the thing that built prod's `tracks` table. Also gone; the surviving script
  is `scripts/_sync_local_to_turso.py`.

### 4.2 Stale docstrings that describe a pipeline that no longer exists
**verified:**

- `backend/app.py:3068` — `/api/ingest/single` claims it "Reuses the
  `_process_track` pipeline (Modal ML → librosa fallback → iTunes preview
  lookup) so features/audio path are populated". `_process_track` has written
  metadata only since `1be1c59`.
- `scripts/run_ingest_v2.py:9-17` — the module docstring lists four "wired
  stages" and says "youtube is independent". There are seven, and youtube is
  armed by fuse at the end of a strictly sequential chain
  (preview → download → librosa → classify → language → fuse → youtube).

### 4.3 The production image ships a 42 MB copy of the developer's SQLite database
`Dockerfile:35`, `.gcloudignore:2` and `.gcloudignore:56` (`data/vibescape.db.*` is excluded, the file
itself deliberately kept)

**verified.** `COPY data/vibescape.db /app/seed/vibescape.db` bakes the local
DB into the runtime image. `docker-entrypoint.sh:7` skips seeding it entirely
when `DB_BACKEND=turso`, which is what `deploy.ps1` sets — so in production it
is never read. Meanwhile it carries the `users` table (3 rows, including
Spotify emails) and the `sessions` table (18 live bearer tokens, which per 3.3
never expire), uploaded to Cloud Build and readable by anything that can pull
the image. Make the `COPY` conditional on a build arg, or drop it and let the
entrypoint fail loudly when a local run has no DB.

### 4.4 `ingest/db.py` is dead code that shadows `backend/db.py`
`ingest/db.py` (whole file)

**verified** — nothing imports `upsert_track`, `track_exists_by_spotify_id`,
`update_audio_path` or `update_spotify_id`. Worth deleting rather than
leaving: the module is named `db`, and `backend/app.py:42` puts `ingest/` on
`sys.path`, so a future `import db` resolves to whichever directory wins. The
file is also a trap on its own terms — `upsert_track`'s UPDATE branch writes
all 20 `_TRACK_COLS` from `track_data.get(c)`, nulling every column absent
from the caller's dict.

### 4.5 `_collect_tracks` is unreachable
`backend/app.py:2814`

**verified** — superseded by `_iter_tracks` (`backend/app.py:2773`) in
`d2440a8`; `_run_ingest_job` only calls the streaming form. `fetch_liked` /
`fetch_top_tracks` in `ingest/spotify_library.py:221-228` are its only
remaining consumers and are themselves unused.

### 4.6 The `/callback` bridge's return-path table is a raw object lookup
`backend/app.py:1686-1687`

**verified.** `RETURN_PATHS[state] || '/'` with `RETURN_PATHS = { vs_next: '/' }`.
A `state` of `constructor`, `toString` or `valueOf` hits `Object.prototype`
and yields a truthy function, so `dest` becomes a function and
`window.location.replace(dest + '?' + qs)` navigates to stringified source.
Not an open redirect — the comment is right about that — but not the "lands
on the default" the comment promises either. Use
`Object.prototype.hasOwnProperty.call(RETURN_PATHS, state)`.

### 4.7 Jobs are never reaped despite a comment saying they are
`backend/app.py:2333`

**verified.** `_update_job`'s docstring says it "silently ignores a job that
has been reaped", but nothing reaps: `JOBS` only loses entries via the two
`JOBS.pop` calls in `/api/ingest/single` (`backend/app.py:3140`,
`backend/app.py:3144`). Every completed sync job leaks its dict for the life
of the process. Add a TTL sweep, or delete the claim.

---

## 5. Listening events — what now exists, and what is left to do

Added 2026-10-02. Before this, `user_tracks.play_count` and
`user_tracks.last_played` had **no writer at all** — `SUM(play_count)` was 0
across 5,475 rows — so `/similar` had no per-user signal to personalise with.

### What landed

**`POST /api/events`** (`backend/app.py`, "Listening events (telemetry)"
section, just above the admin routes). Bearer-authenticated, inline, no
background thread of any kind — Cloud Run's CPU throttling and scale-to-zero
make an in-process worker the same trap as 1.5. Per request: 2 SELECTs to
batch-resolve `spotify_id`/`track_id` against the catalogue, `ceil(n/25)`
multi-row INSERTs into `track_events`, `ceil(distinct_tracks/20)` upserts
into `user_track_stats`. Batch cap 50; the overflow is counted in `rejected`,
the request is not failed.

**It always answers 202** `{"accepted": int, "rejected": int}` — malformed
body, malformed event, unknown track, missing table, any exception. The only
non-202 outcome is a 401 from `require_user`. Telemetry must never be able to
break playback. **[frontend contract]** — the frontend is built against
exactly this shape.

**`track_events`** (`schema.sql`) — append-only, one row per client event:
`user_id, track_id, type, reason, position_ms, duration_ms, vibe,
vibe_source, dj_mode, source, client_ts, server_ts`. Indexed
`(user_id, id DESC)` and `(user_id, track_id, id DESC)`. **No UPDATE, no
DELETE, ever.** The skip (`type='play_end', reason='skipped'`) is the only
explicit negative the system gets, which is why this is a log and not a
counter.

`vibe_source` ('user' | 'system' | NULL) records **who** put the number on
the slider: the person dragging it, or DJ mode echoing back its own choice
(`frontend-next/src/state/PlayerContext.jsx:150` vs `:178`). Unlabelled is
NULL and is never defaulted — a wrong label is worse than a null, because
mislabelled system echoes train the recommender on its own output.

**`user_track_stats`** (`schema.sql`) — materialised per-(user, track)
aggregate of `track_events`, written inline by the same request. This is what
`/similar` reads; it cannot scan an event log per candidate. Label-agnostic
volume (`play_count`, `end_count`, `total_played_ms`, `dj_play_count`,
`first_played_at`, `last_played`, `last_skipped_at`) plus two parallel
families that must never be averaged together:

- `u_*` — `vibe_source='user'`: **preference**. What this person reaches for
  and at what slider position.
- `s_*` — `vibe_source='system'`: **reward**. DJ mode picked the vibe and the
  track; finishing vs killing it early is a verdict on the recommender.

Each family carries `play_count, end_count, complete_count, skip_count,
replace_count, skip_position_ms_sum, vibe_count, vibe_sum, vibe_sum_sq`.
Counters are primitives — ratios (completion rate, skip rate, mean bail
point, mean and variance of the vibe it is played at) are derived on read,
per family. Events with no `vibe_source` land in the totals only, so
`u_* + s_*` can legitimately be less than `play_count`.

**Rules that keep the duplication safe.** Events are truth; the aggregate is
a cache of the events and nothing else. `POST /api/events` is the only writer
of either table. Because Turso has no transactions (2.2), the insert and the
upsert are independent statements and the cache can drift —
`scripts/rebuild_user_track_stats.py` (dry-run by default, `--apply`,
`--turso`, `--user N`) recomputes every row from `track_events` alone, so
drift is an annoyance rather than data loss. `backend/app.py`'s
`_accumulate_stats` is the spec; the script's SQL is its restatement, and the
two must be changed together.

### Deliberately not done

`user_tracks.play_count` / `last_played` are **not** maintained. They stay at
0 and are now dead columns. A second writer with its own idea of what a
"play" is, disagreeing with the event log, is 1.2's exact failure mode; and
`user_tracks` is the library-membership table, so counting a DJ/autoplay play
of a catalogue track there would mean fabricating a library membership. The
one reader, `backend/app.py`'s `/api/admin/users/{id}/tracks`, now LEFT JOINs
`user_track_stats` instead and additionally returns `last_played`.
**[frontend contract]** — additive field on an admin-only response.

Left in place, worth doing: delete the two `user_tracks` columns, and teach
`scripts/_dedupe_local_by_isrc.py` / `_dedupe_turso_by_isrc.py` to fold
`user_track_stats` and `track_events` onto the surviving track id. Today they
merge `user_tracks.play_count`, which is the dead copy — so deduping after
telemetry is live silently strands a track's listening history on the
dropped row.

### What the next person has to do

1. **Create the tables on Turso.** `ensure_db()` returns immediately on
   `DB_BACKEND=turso` (`backend/db.py:933`), so `schema.sql` never reaches
   production. Run `python scripts/_turso_create_event_tables.py` — it lifts
   the DDL straight out of `schema.sql` so the two cannot drift. Until it has
   run, prod answers 202 with everything in `rejected` and logs
   "no such table: track_events" per batch. **This must happen before the
   next deploy of the frontend that posts events.**
2. ~~**Make `/similar` read it.**~~ — DONE 2026-10-03, for recency only.
   `_similar_dj` and `_similar_vibe` now re-rank by a subtractive recency
   penalty (`docs/dj-recency-plan.md`, implemented). It reads `last_played`,
   `last_skipped_at` and the summed `u_+s_` skip counters, and nothing else.
   Summing the two skip families is safe **because every term is negative** —
   the rule is positive terms read `u_` only, negative terms may read both.
   The preference half (a term that *raises* a score from the `u_*` family)
   is still not built, and per finding B below it would read all zeros today.
3. **Use `s_*` to grade DJ mode**, which is currently unmeasured —
   `s_complete_count / s_end_count` per user is a direct completion rate for
   recommendations the DJ chose.
4. **Retention.** `track_events` grows without bound and nothing prunes it.
   Not urgent at current volume, but the aggregate is what queries read, so
   old raw events can be rolled off once there is a reason to.
5. **Backfill is impossible.** There is no historical play data anywhere —
   the counters start from the first event posted after this ships.

### Found while planning the DJ recency re-rank (2026-10-02)

Plan: `docs/dj-recency-plan.md`, **implemented 2026-10-03**. Findings that
outlive it:

- **`total_played_ms` is identically 0 for completed plays.** **verified**,
  20/20 local events: every `play_end` with `reason='completed'` arrives with
  `position_ms = 0` and `duration_ms = NULL`; only skips carry a real position.
  `frontend-next/src/lib/listenLog.js` `endPlay()` already anticipates the
  Spotify playhead-reads-zero case but guards the fix on `duration != null`,
  and the wall-clock fallback above it only fires when `position == null` — a
  reported finite 0 slips through both. **[frontend contract]** — the fix is in
  a frontend-owned file. Until then `total_played_ms` is not a listening-time
  measure and nothing may be derived from it; skip depth must come from
  `*_skip_position_ms_sum` against `tracks.duration_ms`.
- **The `u_*` family is empty.** **verified**, 20/20 local events are
  `vibe_source='system', dj_mode=1`. Item 2 above ("`/similar` should read the
  `u_*` family for preference") would read all zeros today. The preference half
  of the split is unvalidated end to end; confirm it with one manual slider
  drag before building on it.
- ~~`last_played` is written on every accepted event including a skip~~ —
  FIXED 2026-10-06. `last_played` is now written ONLY on a qualified listen:
  `play_end` with `reason='completed'` OR `position_ms >= DJ_QUALIFIED_PLAY_MS`
  (default 90000 ms). Skips go to `last_skipped_at` only. The library endpoint
  returns `MAX(last_played, last_skipped_at)` under the pre-existing
  `last_played` field (no frontend change). `scripts/rebuild_user_track_stats.py`
  mirrors the predicate. Turso rollout note: no schema change, no new column,
  no migration — a `--turso --apply` rebuild after deploy is optional, only
  needed if you want the historical `last_played` recomputed under the new
  rule (otherwise old qualified listens plus pre-change short-skip timestamps
  stay in place, biasing recency slightly toward "touched").


## 6. DJ recency re-ranking — shipped 2026-10-03, what is left

Implemented per `docs/dj-recency-plan.md`. `final = score - W*(score[#1] -
score[#limit]) * P`, `P >= 0`, so a user with no `user_track_stats` rows gets
the pre-feature ordering exactly (verified by running it). Tunables:
`DJ_RECENCY_HALFLIFE_H` (72), `DJ_SKIP_HALFLIFE_H` (168),
`DJ_RECENCY_WEIGHT` (2.0, **0 is the kill switch**), `DJ_RECENCY_POOL` (150),
`DJ_RECENCY_POOL_MAX` (300), `DJ_RECENCY_SKIP_GUEST` (off).

### 6.1 The candidate pool is now 150-300 full track rows per request
`backend/app.py` (`_similar_dj` Turso branch, `_similar_vibe`)

**verified by reading, not measured against Turso.** Both queries now
`LIMIT` the pool rather than `limit`, and both select all ~64
`TRACK_COLUMNS` including `mfcc_json` and `chroma_mean_json`. That is
plausibly 150-400 KB per recommendation over the wire instead of ~10 KB, on
one round trip. The DB-side cost is unchanged (there was never an ANN index;
it was already a full scan). If this shows up in Cloud Run latency the fix is
the two-stage fetch the plan costs out in §4: id + distance + stats for the
pool, re-rank, then a second query for the winning `limit` ids. Instrument
before switching — two round trips may well be worse than one fat one.
**Nobody has timed this.**

### 6.2 The constants are untuned and must stay that way for now
Shipping defaults is deliberate. The local DB holds 20 events spanning 35
minutes from one user; 72 h cannot be estimated from that. Do not touch the
half-lives until there are ~2,000 events over at least two weeks and at least
two distinct listening days per user. The one tuning that is possible today
is the offline rank-displacement check with `explain` on, which needs no
event data.

### 6.3 `_similar_vibe` is re-ranked on every call, not only as a DJ fallback
`backend/app.py` (`_similar_vibe`)

**verified by running it.** `GET /api/tracks/{key}/similar` and the POST
`mode=vibe` path go through the same penalty. That is the plan's intent (the
DJ falls back here exactly when repeats are most likely), but it means a
plain "more like this" list also reorders for a user with history.
**[frontend contract]** — ordering on an endpoint that was previously a pure
function of the two tracks' features.

### 6.4 Guest history is still shared
`backend/app.py:700` (`auth_guest`), gate in `_dj_recency_enabled`

**verified.** All guests key on one `users` row, so one guest's plays and
skips suppress tracks for every other guest. Accepted at demo volume; set
`DJ_RECENCY_SKIP_GUEST=1` to opt the row out. Watch if guest traffic reaches
a few hundred plays a week — the symptom is "the demo recommends weird
tracks" with no obvious cause.

### 6.6 Per-user dynamic H_play landed; needs Turso table + rebuild on deploy
`backend/app.py` (`_fetch_user_halflife`, `_upsert_user_stats`),
`schema.sql` (`user_stats`), `scripts/_turso_create_user_stats.py`,
`scripts/rebuild_user_stats.py`

**verified by running on local sqlite 2026-10-06.** `user_stats` caches
per-user play cadence (EMA of inter-play interval, play_count gate at 5);
`_recency_penalty` now takes `halflife_h` and the DJ handlers thread
`H_play_user = clamp(3 * ema, 12, 336)` through for users past the gate,
falling back to the fixed 72 h env default otherwise. A user with no row
(cold start, Guest, missing table, read error) is a strict no-op --
property #1 is preserved.

Deploy preconditions (none have run against Turso yet):
1. `python scripts/_turso_create_user_stats.py` -- creates the table. Until
   it has run, `_fetch_user_halflife` logs "no such table: user_stats" per
   DJ/vibe call and silently uses the env default (safe degrade).
2. `python scripts/rebuild_user_stats.py --turso --apply` -- optional but
   recommended; the write path only arms on *new* play events, so without a
   rebuild everyone stays cold-start until they rack up 5 fresh plays.

Known: the user_track_stats upsert and the user_stats upsert are now two
separate statements on top of the event INSERT (three on Turso, no
transaction). Same drift mode as 5's user_track_stats; same recovery
(rebuild_user_stats.py).

### 6.5 Still no automated tests
The three verification scripts for this change lived in the scratchpad and
were not committed. `_recency_penalty` and `_rerank_by_recency` are pure, take
no connection, and are the first things in this repo that could trivially
carry a unit test. Worth being the first `tests/` directory.


## 7. DJ session replay — landed 2026-10-10 (branch `dj-replay`), what is left

The client now POSTs its raw event log (`events`) and `backend/dj_replay.py`
replays it into the query, centred on the user's library mean. Offline, on
local `track_events` 2026-10-02..09: finished-vs-skipped AUC 0.83 vs 0.64 for
the old weighted sum, and after a search 5.5 of the top 10 sit in the
searched track's neighbourhood vs 1.4. The harness that produced those
numbers is local only (`.claude/tmp/dj-compare/`, gitignored).

### 7.1 `user_embedding_mean` must be created on Turso before deploy
`schema.sql` (end), `scripts/_turso_create_user_embedding_mean.py`

**verified.** `ensure_db()` no-ops on Turso. Without the table the replay
still works — `_dj_library_mean` logs a warning and keeps the mean in process
memory — but every cold instance re-reads the user's whole library of vectors
on its first DJ request. The script takes credentials from the environment,
not from `_load_gcp_secrets.ps1`.

### 7.2 The legacy `positive_ids` path still drops the seed's own verdict
`backend/app.py`, the `else:` branch of `_similar_dj`

**verified.** Kept byte-for-byte for old clients. It removes `anchor_id` from
positives and negatives, and the seed is the current track: a search-and-play
never shaped the recs while it played, and at autoplay the outgoing track's
own skip was dropped from the fetch that picked its successor (46% of 655
replayed fetches). Delete the branch once no client sends `positive_ids`.

### 7.3 Different songs share one preview, so one embedding
**verified on the local DB, 2026-10-10.** 43 groups (97 tracks) of
*different* songs by the same artist have byte-identical `fused_embedding`,
and in all 43 every row has the same `preview_url` — e.g. Frank Ocean's
*Nights* / *Nikes* / *Lost* / *White Ferrari*. Their vibe scores and DJ
neighbours are those of whichever song the preview really is. (Another 37
groups are the same song twice, which is the dedupe question, not this.)
**suspected** cause: the preview stage's search fallback accepting an
artist-level match. Cheap check: re-run preview resolution for tracks 2111
and 2484 and compare what each candidate source returns.

### 7.4 The replay constants are starting values
`backend/dj_replay.py` (module top)

Set before the offline replay and not tuned on it. The cap on `eta` for a
finished track is reached at about 0.45 cosine to S, so passive listening may
turn out to steer too easily — `SURPRISE_GAIN` is the knob.

### 7.5 Explore mode after a run of skips is not built
The design calls for widening the pool and spreading the picks after three
or more skips in a row. Not implemented: the logs hold no data to test it on.

### 7.6 [frontend contract] `score` is now cosine against a centred query
In replay mode `score` is still a cosine, but against the centred query, so
values sit around 0.3 rather than 0.9. Nothing in `frontend-next/` reads it
(grep, 2026-10-10), and the re-rank's lambda scales with the score window, so
it needs no recalibration.

## 8. Language became a database-queue stage — landed 2026-10-10, what is left

Whisper is out of the pipeline. `ingest_pipeline/stage_language.py` no
longer loads a model, reads audio or touches the GPU; it parks every live
row at `language_status='pending'` and a pipeline run **stops there**. A
Claude Code session queries the DB for those rows, reads title/artist/album
and writes the tag through `ingest_pipeline/language_tagging.py`, which also
fires the `fuse_status='pending'` cascade. `ingest_pipeline/README.md`
§ Tagging languages is the interface document.

**`fuse` and `youtube` now gate on `language_status='done'` exactly**
(verified by running both `fetch_pending`s against each status on a copy of
the local DB, 2026-10-10). `'whisper_done'` and `'no_match'` were removed
from both IN-lists: neither has a producer any more, and the queue flow's
"no language" is an explicit clear writing `language=NULL,
language_status='done'`, which passes a `'done'`-only gate on its own merits.

### 8.1 Untagged tracks are now permanently out of the DJ pool
**verified by reading**, and accepted by the user as the trade. Before, a
wrong-but-present Whisper tag let every track fuse. Now nothing fuses until
someone tags it, so the 656 local (and ~1,620 production) rows at
`'pending'` are invisible to the DJ until a session runs.
`python scripts/language_tags.py --status` reports the count, the oldest and
median wait in days, and the number encoded-but-unfused; it exits 1 when
anything is waiting, so it can be a cron check.

### 8.2 `ingest/ml_backend.py`'s Whisper dispatch is now unused
**verified** by grep across `*.py`: `predict_language_from_url`,
`predict_language_from_path`, `_get_local_whisper`,
`_modal_predict_language_from_url`, `_local_predict_language_from_url` and
`_MODAL_LANG_FUNCTION_NAME` have no caller left in this repo.
`ml/src/predict_language.py` imports `whisper` directly and `modal_app.py`
defines its own copy, so neither is a consumer. **Not removed** — flagged
for the owner to decide, since the Modal deployment is a separate artifact.

### 8.3 The hand-driven pair is superseded
`scripts/_llm_verify_export.py` and `scripts/_llm_verify_apply.py` still
exist and still work, but they select on `language_status='whisper_done'`,
which no longer has a producer. They are a second writer of the same
columns with the same cascade — exactly 1.2's failure mode if the two ever
diverge. Delete them once nobody is mid-flight on a hand-driven batch.

### 8.4 Not verified against production
Nothing here was run against Turso. Two things to check before/after a prod
run: `SELECT language_status, COUNT(*) FROM tracks GROUP BY 1` (prod rows
land NULL, not `'pending'`, because its `tracks` has no column defaults —
`LanguageStage` normalises them, but only for rows a cohort reaches), and
whether any `'whisper_done'` rows exist there (local has none).

## Checked and clean

Recorded so the next audit does not redo them.

- **Orphaned call sites.** Every module under `backend/`, `ingest/`,
  `ingest_pipeline/`, `scripts/` and `ml/` was parsed with `ast` and its
  called names diffed against defined + imported + assigned names, then again
  across modules for `from X import Y` where `Y` is not defined in `X`, and
  for `self.X` against each class's members. The only hits are false
  positives (`__file__`, `except … as e` handler names, and
  `pl.LightningModule` attributes in `ml/src/model.py`). **`1be1c59` was the
  only instance** of the `_update_job` / `_bump` / `_is_cancelled` failure
  mode, and it is repaired at `backend/app.py:2332-2354`.
- **Operational scripts default to dry-run.** `_sync_local_to_turso.py`,
  `_dedupe_turso_by_isrc.py`, `_fix_language_tags.py`, `_llm_verify_apply.py`
  and `language_tags.py` all require an explicit `--apply` and print
  "dry-run — nothing was modified" otherwise.
- **Secret hygiene.** `scripts/_load_gcp_secrets.ps1` is excluded from the
  deploy upload by `.gcloudignore`'s `scripts/_*.ps1`, and from the image by
  `.dockerignore`'s `scripts/`. It is tracked in git, which remains the real
  problem — out of scope for this backlog, but it should be rotated and moved
  to Secret Manager.
- **SQL injection.** The only string-interpolated SQL fragments are
  `?`-placeholder counts and the DJ exclude list at `backend/app.py:1387`,
  which is coerced through `str(int(x))`. Clean.
- **Path traversal.** `serve_react`'s containment check
  (`backend/app.py:3748`) and `_resolve_audio_path` (`backend/app.py:1708`)
  both resolve before comparing. Clean.
- **Mid-chain `no_match` stalls.** `librosa`, `classify` and `fuse` declare no
  `finalizes` entry for `no_match`, which would strand a row at
  `ingestion_status='pending'` and make `PreviewStage` re-select it every
  pass. None of the three ever returns `STATUS_NO_MATCH` today, so it is not
  reachable — but any new `no_match` return in those stages needs a
  `finalizes` entry added with it.
