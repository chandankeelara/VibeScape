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

### 1.1 ~~`/api/recompute-scores` rewrites every track in the database, for every user~~ — DONE (2026-10-13), writes batched 2026-10-15
`ingest/score_recompute.py`, `scripts/run_ingest_v2.py`, `backend/app.py` `api_recompute_scores`

**verified by running the tests; covered by
`tests/backend/test_recompute_scores.py` and
`tests/ingest/test_runner_recompute.py`.** The recompute is global by nature
(one z-score baseline over the shared catalogue), so the fix is about who
runs it and how often, not a per-user scope:

- The three import jobs no longer run it. They only add metadata-only
  `pending` rows, so the baseline could not move until the offline pipeline
  scored them; each sync paid ~3,800 Turso round trips for nothing.
- It moved out of `app.py` into `ingest/score_recompute.py` (no FastAPI
  import) and the dead `user_id` parameter is gone. `run_ingest_v2.py` runs
  it when `librosa` or `classify` finished a row: at the end of a single
  pass, and under `--loop` **once when the loop drains**, not per pass.
  Each scored track shifts the mean and std, so a per-pass recompute
  rewrites ~every row every pass (~76,000 round trips to drain 1,000 tracks
  at `--batch 50`). A failed one is retried at the next idle point.
  `--recompute-only` runs it by hand.
- Rows whose stored activation, valence, activation_relative, vibe_score and
  mood already match (1e-6 tolerance) are not written, so a re-run over an
  unchanged catalogue costs one SELECT.
- `POST /api/recompute-scores` is `require_admin`, and its docstring says
  global. No caller in `frontend-next/`; `mobile/` has an unused wrapper
  (see 4.9). The response gained `written` and `unchanged`.

~~**What remains:** a recompute after new tracks were scored shifts the mean,
so most rows still get one UPDATE each, one HTTPS round trip apiece on Turso.~~
DONE (2026-10-15): the UPDATEs go out through `execute_batch` 200 at a time,
one round trip per chunk (`tests/backend/test_recompute_scores.py`).

### 1.2 `vibe_score` has two writers that disagree on what it means
`ingest_pipeline/stage_classify.py:123` vs `ingest/score_recompute.py`

**verified.** ClassifyStage writes `vibe_score = activation` (the raw 0–100
axis). `score_recompute` writes `vibe_score = rel`, the z-scored
`activation_relative`, from the librosa features rather than MERT. Since
2026-10-13 the runner recomputes at the end of any pass that classified
something, so a classified track's ML-derived `activation` / `vibe_score`
survive only until the end of that pass (previously: until the next sync).
In practice this matters less than it looks: the vibe filter and the UI
badge prefer `vibe_score_ml`. Any UI or
query that compares `vibe_score` across tracks ingested at different times is
comparing two different quantities. **[frontend contract]** — whichever
definition wins is the one the client has been rendering.

It reaches the DJ too. **verified by running it on a copy of the local DB
(2026-10-15):** `vibe_score`, `activation` and `valence` are three of the
fused vector's nine scalar inputs (`fused_vector.SCALAR_COLS`), and the
recompute rewrites them without re-arming fuse. Re-fusing 20 finished tracks
changed only the scalar block (MERT and language blocks bit-identical; cosine
to the stored vector 0.988 to 0.9998), so stored vectors carry scalars the
row no longer has, and a re-fuse after a language correction also moves the
track by that drift. Whoever settles the definition should decide whether
the recompute re-arms fuse.

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

### 1.6 ~~`FuseStage` cannot run against Turso: it reads the MERT blob raw~~ — FALSE: was never broken for mert (3072 bytes needs no padding); the decode fix is for fused (3152 bytes)
`ingest_pipeline/stage_fuse.py:86`

**verified on production** by a read-only probe from the owner's session,
2026-10-15: a `mert_embedding` (server `LENGTH()` 3072) arrives as 4096
base64 chars with no padding, and both plain `base64.b64decode` and the old
`db_client` reader return 3072 bytes, so fuse's raw read of the 768-d MERT
blob works today. A `fused_embedding` (`LENGTH()` 3152) arrives as 4203
chars, also unpadded, and the old reader returned 0 bytes; that is what 2.1
fixes. The analysis below assumed every blob read empty. Kept for the
record:

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

### 1.7 ~~A fresh local SQLite bootstrap dies in `_seed_default_user`: `users` has no `pin_hash`~~ — DONE (2026-10-10)
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

**Fixed 2026-10-10**, together with the bug it was hiding: with the INSERT
fixed, a fresh DB died on its *third* `get_conn()` instead. `_migrate` added
`tracks.user_id` unconditionally, which flipped a schema.sql database onto the
legacy per-user path, which rebuilt `tracks` without `language`, and
schema.sql's language index then failed. That ALTER is gone (production has no
`tracks.user_id`). Existing dev DBs already have the column and keep the legacy
shape — so **local dev DBs still differ from production** (`vibe_score NOT
NULL`, `user_id` on tracks). Regression tests: `tests/database/test_schema.py`.

### 1.8 ~~The iTunes matcher gave tracks another song's audio~~ — DONE (2026-10-12)
`ingest_pipeline/preview_providers.py` `ItunesPreview._pick_best`

**verified by querying the DB and iTunes.** The third tier returned "the first
result with a preview", and a search for one song usually returns its album,
so 183 prod tracks (124 local) carried a neighbour's preview — 82 groups of
different songs with byte-identical MERT vectors, served to each other by the
DJ at cosine 1.000 (EoO / FINA / EL CLúB / Después de la Playa; four *Blonde*
tracks; Icey / Memo). The matcher now accepts only the same recording
(title, a shared artist, length within max(15 s, 10 %), matching version
markers) or nothing. Tests: `tests/ingest/test_preview_matcher.py`.

All 183 were reset in prod on 2026-10-12 (`scripts/_turso_reset_shared_previews.py`,
backup `data/backups/prod_20261012T080039Z.db`): `ingestion_status='pending'`,
preview, audio-derived columns and embeddings cleared, `language_status`
kept `'done'` so they fuse without re-tagging. They are out of `/similar` and
the vibe queues until the offline runners re-ingest them. **Runners must
have this matcher and must not hold a cached `data/audio/<spotify_id>.*` for
them** — the download stage reuses any cached file.

### 1.9 ~~The ISRC dedupe scripts delete listening history~~ — DONE (2026-10-12), prod run pending
`scripts/_dedupe_local_by_isrc.py`, `scripts/_dedupe_turso_by_isrc.py`

**verified by running both versions on a copy of the 2026-10-12 prod
backup.** They re-pointed `user_tracks` only, but `track_events` and
`user_track_stats` also reference `tracks(id) ON DELETE CASCADE`; the old
version passed all its own checks while orphaning 12 events and 6 stats rows
(deleted outright on prod, where the cascade is live). Now events move to the
survivor first and stats are rebuilt from them (`scripts/_dedupe_history.py`);
on the copy: 288 duplicates removed, events 3998 → 3998, stats equal a fresh
rebuild. **Not yet run against Turso, not even dry** — its connection and the
`--turso` rebuild path are untested.

### 1.10 ~~Clearing a library deleted the caller's listening history~~ — DONE (2026-10-13)
`backend/app.py` `ingest_clear`

**verified by reading the code and schema.sql; covered by
`tests/backend/test_ingest_clear.py`.** `POST /api/ingest/clear` deleted the
caller's `user_tracks`, then every track no other user still linked, then its
audio file. On Turso (foreign keys on) `track_events`, `user_track_stats` and
`track_embeddings` cascade from `tracks(id)`, so a clear erased the caller's
history and the track's MERT/fused vectors. The endpoint now removes
`user_tracks` only; `tracks_pruned` and `audio_files_removed` stay in the
body, always 0. Catalogue rows nobody links now accumulate; if they ever need
pruning, that is an offline admin script that keeps history, not this
endpoint.

### 1.11 ~~The runner re-selected rows no stage could act on, and called itself drained~~ — DONE (2026-10-15)
`scripts/run_ingest_v2.py` `select_cohort`

**verified by a test that fails on the old code**
(`tests/ingest/test_cohort.py`: 100 pending rows, two passes at `--batch
50`, the second processed 0). The cohort was the lowest ids with
`ingestion_status` pending/NULL or any stage column `'pending'`. Since
2026-10-10 language never writes `'done'` and fuse waits for it, so rows
through classify sit at `language_status='pending'` + `fuse_status='pending'`
+ `ingestion_status='pending'`: matched, lowest, unmovable. Every pass took
them, processed nothing, and `--loop` slept while every higher id waited.
**Live on production:** a read-only probe from the owner's session on
2026-10-15 found 827 pending rows, 28 of them blocked like this at the head
of the id order and 799 that had never entered the pipeline. The cohort is
now the union of the selected stages' own gates (`Stage.gate_sql`), lowest
`--batch` ids, so a non-empty cohort always has work: one ids-only query
that ORs the gates, so an idle pass costs one scan of ids
(`tests/ingest/test_gates.py`).

### 1.12 ~~The offline pipeline had two sources of truth~~ — DONE (2026-10-15)
`scripts/_sync_local_to_turso.py` (deleted), `scripts/_target.py`

The pipeline ran on a local copy and the sync pushed it to prod; measured
against the 2026-10-12 backup, a push would have nulled 209 prod
`preview_url`s in users' libraries, regressed 74 prod-only language tags and
nulled 74 `youtube_id`s (numbers from the 2026-10-15 decision, not
re-measured here). The runner and `language_tags.py` now take `--target
prod` (credentials from a gitignored `.env.prod`) and the sync is gone.
**Never run against production yet**; see `ingest_pipeline/README.md` § Prod
for the first run.

---

## 2. Will break

### 2.1 ~~The Hrana BLOB read bug is a one-line base64 padding failure~~ — DONE (2026-10-15)
`backend/db_client.py` `_decode_blob`

Fixed: padding restored, both alphabets accepted, an undecodable blob raises
`sqlite3.DataError` instead of reading `b''` (`tests/database/test_turso_adapter.py`).

**The payloads are measured on production** (read-only probe from the
owner's session, 2026-10-15, one row each). `mert_embedding`, `LENGTH()`
3072: 4096 base64 chars, `len % 4 == 0`. `fused_embedding`, `LENGTH()`
3152: 4203 chars, `len % 4 == 3`. Both standard alphabet (`+` and `/`, no
`-` or `_`), no `=`. On the fused one plain `b64decode` raises "Incorrect
padding", `b64decode(s + "=" * (-len(s) % 4))` returns 3152 bytes (so does
`urlsafe_b64decode` with the same padding), and the old `db_client`
returned 0 bytes; on the MERT one everything returns 3072 (see 1.6). So
the text below is right about padding and wrong about the alphabet: it is
the standard one, not base64url (libsql's source agrees, `STANDARD_NO_PAD`
in `libsql-hrana/src/proto.rs`). The fixed reader is that padded standard
decode; the fixed `db_client` itself has not read from production.
`backend/app.py`'s `vector_extract()` reads were deliberately left in place
and can go in a follow-up. Original analysis:

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

### 2.2 ~~Turso has no transactions at all, and `commit()`'s docstring says otherwise~~ — CHANGED (2026-10-15)
`backend/db_client.py` `execute_batch`

`conn.execute_batch([(sql, params), ...])` is now one transaction on both
backends, and on Turso one request (a Hrana `batch`: BEGIN, each statement
conditioned on the one before, COMMIT, ROLLBACK unless COMMIT succeeded).
`commit()` / `rollback()` say what they do (nothing; `rollback()` no longer
sends a ROLLBACK that always failed). The pipeline stages and the score
recompute use it. **Still open:** the two call sites below still write
separately; each is now a one-line `execute_batch` change. Original:

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
- `scripts/_dedupe_turso_by_isrc.py:219-220` deletes a track's embeddings and
  then the track as two separate commits.

Fix is either Hrana batch requests (several statements in one pipeline POST,
which also fixes the round-trip cost left over from 1.1) or baton reuse. Either way,
correct the docstring first.

### 2.3 ~~Every `except sqlite3.IntegrityError` in the codebase is dead on Turso~~ — DONE (2026-10-15)
`backend/db_client.py` `_hrana_error`

A Hrana error code starting `SQLITE_CONSTRAINT` (libsql-server sends exactly
that for every constraint violation) is now `sqlite3.IntegrityError`, so the
signup race (now `backend/app.py:362`) and the pipeline's droppable-fields
retry work on Turso. **[frontend contract]** a signup that loses the race
past the email pre-check now gets 409 `email_taken` from prod instead of a
500, as it already did locally. Covered by
`tests/database/test_turso_adapter.py`. Original:

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

### 2.4 ~~One bad row discards an entire pipeline batch, including GPU work~~ — DONE (2026-10-15)
`ingest_pipeline/base.py` `Stage._write_results`

A stage's batch is one `execute_batch`. If it fails, the error's kind
decides: a transport error is raised with nothing parked (outcome unknown;
every statement is idempotent); a constraint violation's row (`step`) is
set aside, the rest written again, and that row alone gets the
droppable-fields retry or is parked at `<stage>_stage_error` (a finished
track never is: it is left armed, see 3.12); any other
database error (missing column, read-only, locked, failed COMMIT) is
raised with nothing landed and nothing parked. (The first version rewrote
every row alone and parked on any error, so a timeout parked good rows; the
second judged by position, so a row that alone wrote a missing column was
parked; both fixed after review the same day.) `--retry-failed` re-arms
parked rows (2.5). Classify and fuse lost their copies of the loop
(a `row_writes` hook carries their `track_embeddings` statement into the
row's transaction). Covered by `tests/ingest/test_stage_writes.py`.
Original:

**verified.** `run_batch` loops `_commit_row_update` over every result and
calls `conn.commit()` *after* the loop. If any row raises (see 2.3 — on Turso
it will), the loop aborts before the commit and local SQLite rolls back every
update already applied in that batch. The rows stay `pending`, so the next
pass re-selects the same cohort, re-runs the same MERT forward passes and
fails on the same row — a loop that burns GPU time indefinitely. Commit
per row, or catch per row.

### 2.5 ~~`--retry-failed` does not exist~~ — DONE (2026-10-15)
`scripts/run_ingest_v2.py` `rearm_failed`

`--retry-failed STAGE|all` sets the rows at `<stage>_stage_error` back to
`ingestion_status='pending'` with the stage's own column `'pending'` and
`ingestion_error` cleared (one transaction), logs the count, then runs as
usual. base.py's two promises are true now. Covered by
`tests/ingest/test_retry_failed.py`. Original:

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

### 3.1 ~~A track with `ingestion_status IS NULL` jams the head of the cohort queue~~ — NOT REAL (2026-10-15)

The check it asked for, `SELECT COUNT(*) FROM tracks WHERE ingestion_status
IS NULL`, returns 0 on production (read-only probe from the owner's session,
2026-10-15) and 0 locally; the app's INSERT writes `'pending'` literally.
And since 1.11 such a row could not jam a cohort anyway: the cohort only
holds rows some stage's gate accepts.

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

### 3.5 ~~`FuseStage` writes the fused vector with a blind `UPDATE`~~ — DONE (2026-10-15)
`ingest_pipeline/stage_fuse.py` `row_writes`

Now an upsert in the row's transaction, so `fuse_status='done'` always comes
with a vector. Not "fail the row": the one way to lose the row mid-pass is a
reset deleting embeddings, and failing would park a track the reset just
requeued. Covered by `tests/ingest/test_stage_writes.py`. Original:

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

### 3.8 ~70 tracks have no iTunes preview at all and will park at `no_preview`
`ingest_pipeline/preview_providers.py` `default_chain`

**verified against iTunes** (2026-10-12). After 1.8, 71 of the 124 local
re-ingests found no match, and they are genuine: iTunes does not sell
*Blonde* or the recent Bad Bunny albums, and neither `title artist`, a
`songTerm` search, nor an ISRC lookup returns them (Nights, STATS, Icey,
Ladders, EoO, FINA, The Box checked by hand). These songs are unplayable by
the DJ until a second source exists. The chain is iTunes-only on purpose —
Deezer audio shifts MERT embeddings — so the choice (Deezer, or a 30 s clip
from the `youtube_id` already on the row) is a design decision, not a fix.

### 3.9 The same song stored twice under different ISRCs is recommended twice
`backend/app.py` `_similar_dj`

**verified by querying the 2026-10-12 prod backup.** 78 groups share a
preview and name the same recording (Starboy ×2, Save Your Tears ×2, Veera
Raja Veera ×2); only 43 share an ISRC, so the dedupe scripts (1.9) cannot
merge the other 35 — a single vs its `(From "…")` release, typically. The DJ
returns both: Levitating's top neighbour is "Levitating (feat. DaBaby)".
Collapse versions in the result list (normalised title + shared artist, the
same test as `titles_match` / `artists_match`) rather than in the catalogue.

### 3.10 17 junk rows titled `?` share one embedding
`tracks` where `title = '?' AND artist = '?'`

**verified by querying the prod backup.** 16 have `preview_source='itunes'`.
They are excluded from `reset_shared_previews.py` on purpose. Check
`user_tracks` for references before deleting them.

### 3.11 Most prod tracks have `created_at` NULL
`backend/app.py` `_process_track` (new rows stamped since 2026-10-15)

**verified on production** (read-only probe from the owner's session,
2026-10-15): `created_at` is NULL on 4,844 of 6,238 tracks, 789 of the 827
pending, so not only on rows the app inserted, and `tracks` has no column
defaults. `_process_track` relied on `DEFAULT CURRENT_TIMESTAMP` until
2026-10-15 and now writes the value, which fixes rows created from then on
only. The existing NULLs keep `language_tags.py --status`'s oldest / median
wait (it skips them) and the README's `MIN(created_at)` describing the
stamped minority. Candidate backfill: the earliest `user_tracks.added_at` per
track, if prod's `user_tracks` kept its default (not checked). Check first:
how many NULL-`created_at` tracks have a non-NULL `MIN(added_at)`.

### 3.12 A finished track that fails a stage every pass is retried forever, and enough of them stall a run
`ingest_pipeline/base.py` ("Writing a batch"), `scripts/run_ingest_v2.py` `select_cohort`

**by construction (2026-10-15), not observed.** A stage that fails on an
`ingestion_status='done'` track (a re-fuse, a re-armed youtube) never parks
it, so that 'done' keeps meaning every stage passed: the write matches
nothing and the track stays armed. Nothing records the failure but the log
(`row N is finished; left armed for the next pass`), so a track that fails
the same way every time is retried every pass for good. The runner counts
it as no progress, so one such track cannot keep `--until-drained` from
ending or `--loop` from backing off. But `--batch` of them at the head of
the id order fill every cohort: the run ends as drained while higher ids
wait. Candidate fix if it ever shows: the runner keeps the ids left armed
in this run and leaves them out of its later cohorts.

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
  the thing that built prod's `tracks` table. Also gone, and so is its
  successor `scripts/_sync_local_to_turso.py` (retired 2026-10-15, 1.12).

### 4.2 Stale docstrings that describe a pipeline that no longer exists
**verified:**

- `backend/app.py:3068` — `/api/ingest/single` claims it "Reuses the
  `_process_track` pipeline (Modal ML → librosa fallback → iTunes preview
  lookup) so features/audio path are populated". `_process_track` has written
  metadata only since `1be1c59`.
- ~~`scripts/run_ingest_v2.py:9-17` — the module docstring lists four "wired
  stages" and says "youtube is independent".~~ Fixed before 2026-10-15 (it
  lists all seven); the last "all four stages" in its usage went 2026-10-15.

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

### 4.8 ~~`run_ingest_v2.py --help` crashes on a Windows console~~ — DONE (2026-10-15)
`scripts/run_ingest_v2.py:28`

The arrow was the docstring's only character outside cp1252; it is `->` now,
and `PYTHONIOENCODING=cp1252 python scripts/run_ingest_v2.py --help` exits 0.

**verified** (2026-10-13, on `dj-replay` before and after 1.1). The parser's
description is the module docstring, which contains `→`; on a cp1252 console
argparse raises `UnicodeEncodeError` printing help. Real runs are unaffected.
`PYTHONIOENCODING=utf-8` works around it; the fix is replacing the arrow with
`->`.

### 4.9 [frontend contract] The mobile `RecomputeSummary` reads fields the backend never sent
`mobile/lib/core/models/recompute.dart`

**verified by reading the code.** It parses `tracks_touched` and
`tracks_skipped`; the endpoint has only ever returned `updated`,
`activation_stats`, `mood_distribution` (now also `written`, `unchanged`).
Nothing in `mobile/lib` calls `recomputeScores()`, and the endpoint is now
admin-only, so the cheapest fix is deleting the wrapper and model.

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

### 8.4 Not run against production; its two checks are answered (2026-10-15)
Nothing here was run against Turso. The two things this asked to check were
measured by a read-only probe from the owner's session on 2026-10-15:
`language_status` on prod is NULL 1,145 / done 5,038 / pending 28 /
no_match 27 (its `tracks` has no column defaults, so app rows land NULL;
799 of the NULLs are pending rows that never entered the pipeline, which
`LanguageStage` normalises as cohorts reach them), and there are 0
`'whisper_done'` rows (local has none either).

## 8. Telemetry v2 — landed 2026-10-10 (branch `dj-replay`), what is left

One envelope for every client event (session_id, tz_offset_min, vibe
context, client_ts). Track events — play_start / play_end / pause / resume /
seek / queue_add — go to `track_events`, which gained `listened_ms`,
`end_trigger`, `playback`, `session_id`, `tz_offset_min`, `data`. App events —
session_start / session_end / search / vibe_change / dj_toggle — go to the new
`user_events`. `user_track_stats` gained `total_listened_ms`. New read
endpoint `GET /api/me/stats`.

### 8.1 Turso must be migrated BEFORE the v2 backend deploys
`scripts/_turso_migrate_telemetry_v2.py`, then
`scripts/rebuild_user_track_stats.py --turso --apply`

**verified** (by reading the insert path). The v2 INSERT names the new
columns, so on an unmigrated Turso every POST /api/events fails its insert
and the batch is dropped — behind the endpoint's unconditional 202, so
nothing looks wrong. Local SQLite migrates itself (`_migrate_telemetry`).

### 8.2 [frontend contract] `source` gained 'pick', so old 'search' rows are mixed
**verified.** A song played off a list (a rec, the trail, prev) is now
`source='pick'`. Before 2026-10-10 those were `'search'` (or `'dj'` for a rec
clicked in DJ mode). Any analysis of `source` must split at that date.

### 8.3 Only plays feed the aggregate — keep it that way
`backend/app.py` `_PLAY_TYPES`, `_accumulate_stats`,
`scripts/rebuild_user_track_stats.py` `_expected`

**verified.** `_accumulate_stats` used to treat every row that was not a
play_start as a play_end. With pause / seek / queue_add rows in the same
table that would have counted each pause as an ended play. Both the inline
path and the rebuild now filter to play_start / play_end, checked equal on a
DB copy (rebuild dry-run: 0 missing, 0 differing, 0 stale).

### 8.4 last_played now qualifies on heard time
`last_played` requires `completed` or **listened_ms** >= 90 s (playhead on
pre-v2 rows). Seeking to 2:00 and stopping no longer counts as a listen, so
the DJ recency penalty sees slightly fewer "recently played" tracks.

### 8.5 Old rows stay approximate
Pre-v2 play_end rows have no `listened_ms`; they count their playhead, which
over-reports paused wall-clock time when no playhead was readable. 11 of 144
local completions also recorded 0 ms (the bug fixed earlier). Not
recoverable; the error shrinks as v2 data accumulates.

## 9. Import any public playlist by link — built 2026-10-10, what is left

`POST /api/ingest/playlist-url` + `backend/playlist_import.py`: Spotify,
Apple Music, YouTube / YT Music and Amazon Music links are read without the
user's Spotify login, matched to Spotify, and imported through
`_process_track` (pending rows, as with library sync). Deezer links are
recognised and refused with `deezer_not_supported` (built and measured —
public API with ISRCs, 15/15 — then dropped from the first version by
product decision). Measured live before the rate limit below (with the older 3-query
ladder): matching 15/15 for Spotify and Apple samples, 95/100 on a YouTube
chart playlist. Tests in tests/backend cover it with fakes.

### 9.1 The app's Spotify quota is shared with production — don't spend it testing
**verified 2026-10-10.** ~650 Spotify calls in ~30 min of test imports earned
a `429` with `Retry-After: 70320` (19.5 h) for this client id, which
production uses too. The importer now spends nothing on Spotify links (embed
ids) or songs the catalogue has, ONE search per new song (two for an
ambiguous YouTube title), paces calls
0.35 s apart, and stops a job on a long Retry-After. **Use a separate Spotify
app for local development.**

**One gate for every api.spotify.com call:** `ingest/spotify_guard.py`
(server) and `frontend-next/src/lib/spotifyGuard.js` (browser, kept in
localStorage). The first real 429 records "blocked until T"; until T every
call is answered locally with the same 429, with no request, so existing 429
handling runs without spending more quota. Retry loops use `should_retry()`,
which is true only for Retry-After ≤ 30 s (`spotify_matcher._search` used
to sleep the full Retry-After, which would have been 19.5 h). `GET
/api/spotify/status` reports `{blocked, retry_after_s}` without calling
Spotify. Limits: a block is per process (each process learns it from one
call of its own) and per browser; `accounts.spotify.com` (token refresh)
is not gated. **New Spotify calls must go through the guard**, never bare
`requests` or `fetch`.

### 9.2 One full live run is still owed
After the block lifts (~06:15 UTC 2026-10-11, from a Retry-After of 70320 s
seen at 10:43 UTC): one import per service on a DB copy, once, to confirm
the paced, one-query path end to end and measure its match rate.

### 9.3 Amazon uses Amazon's private web-player API — by decision, with risks
`playlist_import.fetch_amazon`. Two calls, as the public web player makes for
a signed-out visitor: `POST music.amazon.com/config.json` (anonymous token),
then `POST na.web.skill.music.a2z.com/api/showHome`. Live 2026-10-10: 190/190
and 60/60 songs, matching the counts on the page. Product decision, taken
knowing that:
- it is undocumented and against Amazon's terms, and can change or be closed
  without notice — every failure maps to `source_unreadable` /
  `playlist_not_found`, never a crash;
- `AMAZON_APP_VERSION` and the header set are copied from the web player and
  may need refreshing if Amazon starts rejecting them;
- only the North-America endpoint is used; other storefronts and playlists
  longer than 190 songs are untested;
- keep it user-triggered — heavy use from one server IP could get it blocked.

### 9.4 Known limits
- Spotify links without the user's Spotify login read the public embed, which
  stops at 100 songs (the job's `note` says so).
- Apple playlists over 100 songs and user `pl.u-` playlists are untested.
- None of the supported services give ISRCs, so matching is by title +
  artist + duration; misses are listed (`unmatched`), never guessed.
- Same thread + JOBS-dict machinery as the other imports (backlog 1.5).

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
- **Operational scripts default to dry-run.**
  `_dedupe_turso_by_isrc.py`, `_fix_language_tags.py`, `_llm_verify_apply.py`
  and `language_tags.py` all require an explicit `--apply` and print
  "dry-run — nothing was modified" otherwise. (`run_ingest_v2.py` is not a
  dry-run script; `--target local` is its default.)
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
