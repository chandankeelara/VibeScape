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

## Refactor in progress — splitting `backend/app.py` (started 2026-10-25)

`backend/app.py` was ~6,000 lines and 49 routes (5,954 before PR 2, 4,508
after PR 2, 2,289 after PR 3, 1,158 after PR 4, 98 after PR 5). It is being moved, without changing any API
behaviour, into `routers/`, `services/`, `deps.py` (auth dependencies),
`track_rows.py` (the track SELECT and dict helpers) and `bootstrap.py`, over
six PRs:

1. **Safety net** (PR #34, merged). No code moves.
   `tests/backend/test_route_table.py` pins every (methods, path), its auth
   dependency and the catch-all's place; characterisation tests cover the
   routes nothing tested (auth, admin, meta, streams, YouTube GET/search,
   library sync and public-playlist jobs, job cancel); `db.get_conn` becomes
   the one connection seam (`app.py` calls `db.get_conn()` and keeps no
   alias; tests patch it with conftest's `use_conn`).
2. **Leaf modules** (PR #35, merged). Code with no route of its own leaves
   `app.py`, unchanged, one move per commit. Module map (all under
   `backend/`, which is the import root):
   - `bootstrap.py`: `.env` load, `import db`, then the repo root and
     `ingest/` on `sys.path` (that order: `ingest/db.py` would shadow
     `backend/db.py`), the `vibescape` logger, `PROJECT_ROOT`, `AUDIO_DIR`,
     `app_config`, `spotify_creds()`. `app.py` imports it first.
   - `track_rows.py`: `TRACK_COLUMNS`, the genre_scores readiness check,
     `_track_select`, `MOODS`, `_row_to_dict`, `_search_rows` and its cache,
     and (since PR 3) `_isrc_of`, shared by Spotify search and `_link_track`.
   - `services/vectors.py`: MERT and `FUSED_*` constants, column readiness
     (`_fused_v2_ready`, `_fused_v3_ready`), `_variant_spec`,
     `_resolve_anchor`, the embedding loaders, `_l2_normalize`, `_is_turso`.
   - `services/recency.py`: the `DJ_*` env knobs, `_UTS_*` SQL, the penalty,
     re-ranker and the two stats reads.
   - `services/telemetry.py`: event normalisation, the stats rollups,
     `_record_track_events`.
   - `services/jobs.py`: `JOBS`, `JOBS_LOCK`, `_update_job`, `_bump`,
     `_is_cancelled`.
   `services/` is a package, not more flat modules: nothing on `sys.path` is
   called `services`, the Dockerfile copies `backend/` whole, and both ways
   the app starts (`uvicorn app:app` from `backend/`, tests with `backend/`
   on the path) resolve it.
3. **Deps and routers** (PR #36, merged). Module map:
   - `deps.py`: `require_user`, `require_admin`, `require_user_stream`,
     `ADMIN_USER_ID`, `_issue_session`, `_lookup_session`, `_extract_bearer`.
     Imports `bootstrap` first, since `ADMIN_USER_ID` is read from the
     environment at import.
   - `services/spotify_auth.py`: the OAuth code exchange and `/v1/me` read
     behind Spotify login, and the cached client-credentials token
     (`_get_app_token`, `_APP_TOKEN_STATE`) the ingest jobs use.
   - `routers/`: `auth` (sign-up, login, logout, me, Spotify OAuth, guest,
     spotify-link, `/api/spotify/refresh`, `/callback`), `meta` (health,
     moods, demo moods, spotify/config, client-config), `tracks` (list,
     search, random, features, lyrics, `/api/track/{apple_id}/spotify`),
     `spotify` (library manifest, search, status), `stream`, `youtube`,
     `events`, `me` (stats, the heart), `admin` (the four
     `/api/admin/*` routes and `/api/recompute-scores`).
   Still in `app.py`: the ingest routes, jobs and `_process_track` /
   `_link_track` (PR 4), the DJ and both `/similar` routes (PR 5), and the
   SPA routes, registered last.
   **Routers are not added with `app.include_router`.** In FastAPI 0.141.1
   (installed here) `include_router` appends one opaque `_IncludedRouter`
   to `app.routes` instead of copying the routes, which breaks
   `test_route_table.py`, and copying the router's own `APIRoute` objects
   would bypass `app.dependency_overrides`. `routers.Router` records each
   `add_api_route` call and `routers.register(app, router)` replays it on
   the app, the path `@app.get` itself takes; `backend/routers/__init__.py`
   has the detail, `tests/backend/test_routers_register.py` pins it. The
   routes' registration order changed (routers first, then `app.py`'s own,
   SPA last); 1,468 probe paths under six methods resolve to the same
   endpoint as before, and the OpenAPI document is equal except for the
   order its paths are listed in.
4. **Ingest** (PR #37, merged). Module map:
   - `services/library_ingest.py`: `_process_track`, `_link_track` and
     the `_TRACK_MATCH` / `_TRACK_PICK` fragments: the online half of the
     two-phase ingest, also the synchronous path of `/api/ingest/single`.
   - `services/ingest_jobs.py`: the three job workers
     (`_run_ingest_job`, `_run_public_playlist_job`,
     `_run_playlist_url_job`) with `_iter_tracks`, `_playable_first`,
     `_catalog_match`, `_UNMATCHED_REPORT_CAP` and `IngestSources` (the
     sync worker's parameter type). A module apart from library_ingest
     because that one has no thread, paging or job state.
     `services/jobs.py` stays the registry.
   - `routers/ingest.py`: the seven `/api/ingest/*` routes with their
     request bodies; the clear and cancel logic stays in its routes (each
     is the route's body), and the three initial job dicts stay written
     out per route.
   The ingest router is registered after the other routers, so its routes
   now precede the two `/similar` routes still in `app.py` instead of
   following them. No path overlaps: 2,808 probed method and path pairs
   resolve to the same endpoint, and the OpenAPI document is identical,
   path order included. Left in `app.py` by PR 4: the DJ and both
   `/similar` routes, startup and LAN logging, and the SPA routes.
5. **DJ and SPA** (branch `refactor/pr5-dj`). Module map:
   - `services/dj.py`: everything behind `/api/tracks/{key}/similar`, in
     its old order: the request caps, `SimilarBody` (also `_similar_dj`'s
     parameter type), `_similar_vibe`, `_parse_id_weight_list`,
     `_resolve_ids_to_track_ids`, the library-mean cache
     (`_dj_mean_cache`, `_dj_count_embedded`, `_dj_library_mean`),
     `_dj_events_query`, `_dj_song_keys`, `_dj_drop_repeats`,
     `_similar_dj`, `_dj_tag_library`, `_discover_finish`. `_similar_dj`
     moved byte for byte, its Turso `except Exception` fallback to numpy
     (13.1) included.
   - `routers/dj.py`: GET and POST `/api/tracks/{track_key}/similar`,
     registered right after the ingest router, which is where they sat.
   - `routers/spa.py`: the `/next` redirects, the cache constants,
     `_not_modified`, `_static_file` and the catch-all, registered last.
     `NEXT_DIR` climbs one more parent (the file is one directory deeper);
     it still resolves to `<repo>/frontend-next/dist`, and in the image to
     `/app/frontend-next/dist`, where the Dockerfile copies the build.
   - `app.py` is assembly only: the app, CORS, the `register` calls in
     order, and the startup hook with `_lan_ip` / `_log_lan_bind_info`.
     It defines no route.
   Registration order is unchanged: 2,808 probed method and path pairs
   resolve to the same endpoint as at `cbb75b7`, `app.routes` lists its
   paths in the same order, and the OpenAPI document is identical. A real
   `uvicorn app:app` on a scratch SQLite file answered `/api/health` 200,
   both `/similar` routes 401 without a token and `/next/x` 308.
6. **Cleanup PR**: delete the plain re-imports `app.py` keeps only for
   tests; decide the three initial ingest job dicts (a shared
   `jobs.create()` was not done in PR 2: the dicts place their extra keys
   in different positions, so a shared base would reorder the
   `/api/ingest/status` JSON); merge the duplicated session lookup in
   `require_user` / `require_user_stream`. Dead code found by the PR 3
   and PR 4 sweeps, moved or left as it was rather than deleted inside a move:
   `_bearer_token` (now routers/ingest.py) and `_itunes_lookup_by_isrc`
   (now services/library_ingest.py), no callers; `_collect_tracks` (now
   services/ingest_jobs.py, 4.5); app.py's `feat = None`, which nothing
   reads (its comment names an ingest hot path that is offline now);
   `ingest/deezer_client.py`'s docstring, which says `_process_track` calls
   its lookups (it calls no lookup at all);
   `_yt_search_sync` / `_yt_search_with_timeout` (routers/youtube.py, no
   callers: the YouTube GET never searches, so `scripts/prewarm_youtube.py`'s
   "what the endpoint would have produced" is not true of any endpoint), and
   app.py's unused `glob`, `shutil`, `tempfile` and top-level
   `time as _time` imports. Comments that still point at `app.py` for
   code that has left it (found in PR 5, not edited there): `dj_replay.py`
   `replay`'s docstring (`_fused_v2_ready` is `services/vectors.py`),
   `playlist_import.fetch`'s docstring (the Web API attempt is
   `services/ingest_jobs.py`), `scripts/dj_eval/server.py` (the `.env`
   load is `bootstrap.py`), `README.md` (`app.py::_similar_dj`, the tree
   entry for `app.py`), and three frontend comments with stale
   `app.py:<line>` references (`frontend-next/src/lib/vibe.js`,
   `lib/api.js`, `features/queue/dj.js`). Older ones of the same kind:
`db.py` (the get_conn shim's comment), `db_client.py`'s docstring and its
BLOB-read note, `lyrics.py` (the `track_lyrics` cache and
`get_track_lyrics` are `routers/tracks.py`), `playlist_import.py`'s
module docstring, and `services/telemetry.py`'s "POST /api/events (still
in app.py)", which is `routers/events.py`. `docs/dj-recency-plan.md` cites
   `app.py` line numbers too; it is a dated design record, so leave it.

Rules for every move: `test_route_table.py` stays green unedited unless a
route deliberately changes (it should not); patch connections with
`use_conn`, never a module's own `get_conn`; after removing or renaming any
name, diff defined-vs-called names with `ast`, as `1be1c59` taught. The
characterisation tests pin odd current behaviour on purpose (each is marked
ODD in its docstring and has an entry below); fixing one is its own change.
**A name a test patches is read as `module.name` at call time, in the module
that owns it**, never copied with `from module import name`, and `app.py`
keeps no alias of it, so a stale `setattr(app_module, ...)` raises instead
of patching nothing. Patched today: `bootstrap.app_config`,
`bootstrap.PROJECT_ROOT`, `services.recency._DJ_RECENCY_WEIGHT`,
`deps.ADMIN_USER_ID`, `services.spotify_auth._get_app_token` and
`_APP_TOKEN_STATE`, `routers.youtube._yt_manual_search_sync` and
`_YT_SEARCH_TIMEOUT_S`, `services.library_ingest._process_track` and `routers.spa.NEXT_DIR`;
moving one means retargeting its patch in the same commit. Since PR 5
nothing is patched on `app.py`, and tests read only `app_module.app`.
The DJ's mean cache is cleared as `services.dj._dj_mean_cache`. `spotify_library` functions are patched on that module itself,
never through another module's `splib` alias. FastAPI resolves `Depends(fn)` by identity: a
`dependency_overrides` entry must key on the function in `deps.py`.

`file:line` references to `backend/app.py` in the entries below predate the
split; search for the name.

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

### 1.9 ~~The ISRC dedupe scripts delete listening history~~ — DONE (2026-10-12), superseded 2026-10-18 by `scripts/_dedupe_tracks.py` (1.13)
`scripts/_dedupe_local_by_isrc.py`, `scripts/_dedupe_turso_by_isrc.py` (both deleted 2026-10-18)

**verified by running both versions on a copy of the 2026-10-12 prod
backup.** They re-pointed `user_tracks` only, but `track_events` and
`user_track_stats` also reference `tracks(id) ON DELETE CASCADE`; the old
version passed all its own checks while orphaning 12 events and 6 stats rows
(deleted outright on prod, where the cascade is live). Now events move to the
survivor first and stats are rebuilt from them (`scripts/_dedupe_history.py`);
on the copy: 288 duplicates removed, events 3998 → 3998, stats equal a fresh
rebuild. **Not yet run against Turso, not even dry** — its connection and the
`--turso` rebuild path are untested. Production had 270 duplicate ISRC
groups on 2026-10-16, and new duplicates keep arriving (1.13).

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

**First run against production: 2026-10-16 13:18Z** (measured by the lead
session from its log and the backup; not re-checked here). The sequential
`run_ingest_v2.py --target prod --batch 10`, after `backup_prod_db.py`
(`prod_20261016T130348Z.db`, fused-blob check MATCH). Cohort ids 29..375:
preview 8 done / 2 no_match (no 403s, genuine misses), download 8, librosa 8
(22 s), classify 8 (30 s, 25 s of it the one-time MERT load on CUDA),
language nothing to normalise (28 rows wait for a tag), fuse 8, youtube 8
(short-circuited on their existing ids), score recompute scored 4,918 and
wrote 4,872 in 8 s. 71 s in all, exit 0; `ingestion_status` done went 4,882
→ 4,890. `--concurrent` has not run against production (10.1).

### 1.13 Production's `tracks` has no indexes: every gate scans, and the app can insert a track twice (it has) — DONE (app 2026-10-17; prod dedupe + indexes 2026-10-18)
`backend/app.py` `_process_track` / `_link_track`, `schema.sql` (`idx_tracks_spotify_id`, `idx_tracks_apple_id`, `idx_tracks_ingestion_status`, `idx_tracks_isrc`)

**Prod run, done 2026-10-18** (approved by the owner, run from the main
checkout after PR #16 merged, pipeline stopped):

- `scripts/backup_prod_db.py` first, then `scripts/_dedupe_tracks.py --target
  prod` dry run (271 groups, 292 rows to delete, 47 apple_id cleared, 557
  user_tracks on losers: 76 re-pointed, 481 folded), then `--apply`. It ended
  "verified: no duplicates, no orphans, no recording or event lost, stats
  equal a rebuild"; tracks went 6,262 -> 5,970.
- `scripts/_turso_create_track_indexes.py --target prod --apply` then built
  UNIQUE `idx_tracks_spotify_id` and `idx_tracks_apple_id` (partial, NOT
  NULL), plain `idx_tracks_isrc` and `idx_tracks_ingestion_status`; a
  re-run reported all four "exists". So a spotify_id now names exactly one
  row on prod, and the double-insert race is closed by the index.

**App side, done 2026-10-17** (branch `backend/ingest-dedupe`,
`tests/backend/test_process_track.py` and the `_process_track` tests at the
end of `tests/database/test_turso_adapter.py`; nothing run against
production):

- A track whose spotify_id is new but whose ISRC is already in the
  catalogue now links the user to that row (exact spotify_id first, then
  `done` over pending, then lowest id) instead of inserting another.
  "Already in library" counts an ISRC match too.
- Check, insert, link and read back are ONE `execute_batch` (one
  transaction, one request): `INSERT ... WHERE NOT EXISTS (match)`, then
  `INSERT OR IGNORE INTO user_tracks ... ORDER BY <pick> LIMIT 1`, then a
  SELECT of the chosen row. The user's own matching row ranks first, so the
  link's rowcount of 0 is "already in library". Because the write comes
  first, SQLite takes the write lock before the NOT EXISTS reads, and a
  double submit waits and then sees the first row: **no duplicate even
  without a unique index**, so on prod too (tested locally with the index
  dropped and the lock held; reasoned, not run, for libsql, which may
  answer a contended batch with SQLITE_BUSY instead of waiting, failing
  the request without a duplicate). Requests per track: 2 reads + up to 2
  writes before, 1 batch after. Prod `tracks` scans per track, before →
  after: already linked 1 → 3, exists unlinked 2 → 3, new 2 → 3 (the
  "already linked" read on main drove from `user_tracks` and may have cost
  the user's library size rather than a scan). Locally all index lookups;
  schema.sql gains a plain `idx_tracks_isrc` for that, and the prod index
  script should create it too, which makes the 3 cheap there as well.
- An `IntegrityError` from the batch (two connections colliding on the
  local unique index) re-runs it once; its NOT EXISTS then sees the
  winner's row. The `apple_id` index cannot fire: the INSERT never sets
  `apple_id`.
- Two ISRC-blind call sites fixed with it: `/api/ingest/single` read the
  row back by spotify_id (an ISRC-reused row would have been HTTP 500
  `post_ingest_missing`), and `/api/spotify/search`'s `in_library` now
  counts an ISRC match. **[frontend contract]** `/api/ingest/single` can
  return a track whose `spotify_id` differs from the one requested.
- Still keyed by spotify_id alone, left as is: `_playable_first` (only
  orders a sync page, an ISRC-reused `done` row is sorted with the pending
  ones), and the frontend's `markInLibrary` in
  `frontend-next/src/features/search/SearchBar.jsx`, which checks the
  cached library by the requested spotify_id and so prepends a second copy
  of a reused row to its cache until the next refetch (a frontend change:
  compare by `track.id`). Two different spotify_ids with the same ISRC
  added at the same instant still both insert: ISRC has no unique index,
  deliberately.

The original finding:

**verified on the 2026-10-16 backup** (`prod_20261016T130348Z.db`; measured
by the lead session, not re-checked here):

- Production's `tracks` has no index at all: neither of schema.sql's UNIQUE
  `idx_tracks_spotify_id` and `idx_tracks_apple_id`, nor
  `idx_tracks_ingestion_status`. The backup carries every other table's
  indexes, so this is production, not the backup. Every stage gate is a full
  scan there, and nothing stopped `_process_track`'s check-then-insert (a
  `SELECT ... WHERE spotify_id = ?`, then a plain `INSERT INTO tracks`) from
  inserting one track twice when two requests race (fixed app-side above).
- It has happened: spotify_id `1NtUkEglWHG26BOqj3rPhQ` is tracks 6465 and
  6466, both pending, same user (12), source 'search', added in the same
  second (2026-10-15 03:44:10), a double submit. 6466 holds 5 `track_events`
  rows and 1 `user_track_stats` row.
- 73 groups share an `apple_id` and 270 share an ISRC. Of the 804 pending rows
  that never entered the pipeline, 188 share an ISRC with a finished track and
  72 with another pending one: about a quarter of a full drain would be work
  on songs the catalogue already has.

**Production half: scripts written and dry-run on a copy (2026-10-18), not
run on production.** `scripts/_dedupe_tracks.py` (replaces the three ISRC
scripts) and `scripts/_turso_create_track_indexes.py`, both `--target
local|prod` and dry-run by default; tests in
`tests/database/test_dedupe_tracks.py`. Measured on the 2026-10-18 backup
(`prod_20261018T215238Z.db`, 6,262 tracks), working on a copy:

- spotify_id: 1 group, 1 extra row (6465/6466). ISRC: 271 groups, 292
  extra rows (252 pairs, 17 triples, 2 quads). Every spotify_id duplicate
  sits inside an ISRC group, so the union (spotify_id or ISRC,
  transitive) is the same 271 groups and 292 deletions. Keeper:
  `done` first, then the lowest id (`_TRACK_PICK`'s order); 5 groups have
  no finished row and keep their lowest pending one.
- ISRC groups are the same recording: every group's durations within 5 s;
  5 differ in title, all spellings ("Neene Yendigu" / "Neene Yendiga",
  "Taja" / "Tajaa Samachara"); 57 differ in the credited artist (a
  composer vs a singer on the film release).
- apple_id: 211 groups, 233 extra rows, and NOT a merge key: after the
  merge 47 rows still share an apple_id with a different recording (Save
  Your Tears 215 s album cut vs 249 s video edit, Starboy likewise, Kannada
  singles vs their film releases with their own ISRCs), because the iTunes
  matcher gave both the same match. Those 47 get `apple_id = NULL`, as the
  pipeline's own collision retry does locally (`_RETRY_DROPPABLE_FIELDS`);
  their audio and vectors stay. They are the 3.9 problem (the DJ can
  recommend both), not a merge.
- References: `user_tracks` 557 rows on losers, 76 re-pointed and 481
  folded into a row the same user already had (user 12: 202, 13: 266, 19:
  13; earliest `added_at` kept). `track_events` 200 moved.
  `user_track_stats` 71 rows on losers deleted and the keeper's rebuilt from
  the moved events (12 lost 47 rows, 13 lost 1, by folding).
  `track_embeddings` 261 deleted with their rows.
- After `--apply` on the copy: 5,970 tracks, no spotify_id / ISRC /
  apple_id duplicate, no orphan in any of the four tables, per-user event
  counts unchanged (8,091), no user lost a recording, `user_track_stats`
  equal to `rebuild_user_track_stats.py` (0 missing / differing / stale),
  a re-run finds nothing. `_turso_create_track_indexes.py --apply` then
  built all four indexes (and refuses, creating none, on the raw copy).
- Cost on prod: one scan of `tracks`, then per group a scan of
  `track_events` and `user_track_stats` (no index leads with track_id
  there): about 3.5M rows read in total, once.
- Found on the way, fixed in the same PR: once the unique indexes exist,
  `ensure_db()` on a prod copy rebuilt `tracks` into the pre-split legacy
  shape (prod's `tracks` has an unused `user_id` column, which with a
  unique spotify_id index looked legacy), dropping `language` and every
  later column, then failed on schema.sql's language index. Local only;
  `ensure_db` no-ops on Turso. Gated on `user_tracks` not existing.
- Not fixed: `scripts/rebuild_user_track_stats.py --turso` still reads
  `scripts/_load_gcp_secrets.ps1`. The dedupe no longer calls it.

Until production is deduped and indexed, a backup copy still needs two
indexes added before its first local run (`ingest_pipeline/README.md`
§ Prod); after, a plain copy bootstraps.

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

### 2.2 ~~Turso has no transactions at all, and `commit()`'s docstring says otherwise~~ — CHANGED (2026-10-15), `_process_track` DONE (2026-10-17)
`backend/db_client.py` `execute_batch`

`_process_track` (now `_link_track`) checks, inserts, links and reads back
in one `execute_batch` (covered against the fake Hrana server in
`tests/database/test_turso_adapter.py`; not run against a real libsql
server). The dedupe script below is still two commits.

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
- ~~`scripts/_dedupe_turso_by_isrc.py:219-220` deletes a track's embeddings and
  then the track as two separate commits.~~ Replaced 2026-10-18 by
  `scripts/_dedupe_tracks.py`, one `execute_batch` per group (1.13).

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
decides: a transport error, or a busy or locked database, is tried again
three times (2, 4, 8 s) and then raised with nothing parked (a transport
error's outcome is unknown; every statement is idempotent, which also makes
the retries safe); a constraint violation's row (`step`) is
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

### 3.9 ~~The same song stored twice under different ISRCs is recommended twice~~ — DONE in the DJ (2026-10-20, see 12.4); the catalogue still holds both
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

`--concurrent` (2026-10-16) has the same hole one stage at a time: each look
takes the lowest `--batch` ids of that stage's gate, so `--batch` such tracks
at the head of it come back on every look and the stage never gets past
them, while the run counts it idle and drains around it. Same candidate fix,
kept per stage thread.

### 3.13 Without `X-Spotify-Authorization`, `/api/spotify/library` sends the VibeScape session token to Spotify
`backend/app.py` `spotify_library` (`_extract_bearer(spotify_authorization) or _extract_bearer(authorization)`)

**verified** by `tests/backend/test_ingest_jobs.py::test_library_without_the_spotify_header_sends_the_session_token_to_spotify`
(2026-10-25). The "legacy" fallback reads `Authorization`, which
`require_user` already requires to be the caller's VibeScape session, so a
request without the Spotify header hands our bearer token to
api.spotify.com as the access token (and Spotify answers 401, which comes
back as `spotify_token_expired`). frontend-next sets the header whenever it
holds a Spotify token (`src/lib/api.js:38`), so this fires only when it
does not. Fix: drop the fallback and answer 401
`missing_spotify_authorization`. **[frontend contract]**, small: the error
changes for a caller with no Spotify token.

### 3.14 Deleting a user leaves their stats and events behind on SQLite
`backend/app.py` `admin_delete_user`

**verified** by `tests/backend/test_admin_routes.py::test_delete_leaves_the_users_stats_behind_on_sqlite`
(2026-10-25). The manual cascade deletes `user_tracks`, `sessions` and
`users` only. db_client's SQLite connections never enable foreign keys, so
`user_track_stats`, `user_stats`, `track_events` and `user_events` keep
rows for a user id that no longer exists. On Turso the schema's
`ON DELETE CASCADE` removes them (not checked there). Local only; delete
them explicitly in the same batch.

### 3.15 On an empty database the first account is the admin
`backend/app.py` `ADMIN_USER_ID`, `auth_email_signup`

**verified** by `tests/backend/test_auth_routes.py::test_on_an_empty_database_the_first_signup_is_the_admin`
(2026-10-25). `ADMIN_USER_ID` defaults to 1 and admin is `id == ADMIN_USER_ID`,
so on a fresh local or test database whoever signs up first gets admin
rights (so would a first guest session, by the same rule: read from the
code, not tested). Production's user 1 exists, so this is a
dev and fresh-deploy concern. Candidate fix: no default, admin off unless
the variable is set.

## 4. Cleanup

### 4.1 Stale file references
**verified**, all four:

- ~~`backend/app.py:2687` — `_process_track`'s docstring sends the reader to
  `scripts/run_ingest_worker.py`.~~ Fixed 2026-10-17 (1.13).
- `schema.sql:111` — same dead reference, in the two-phase ingestion comment.
- `backend/db.py:926` — `ensure_db` says the Turso schema is managed by
  `scripts/migrate_to_turso_http.py`. That file does not exist, so the
  comment names no actual owner for the prod schema.
- `ingest_pipeline/base.py:19` — cites `scripts/_push_local_to_turso.py` as
  the thing that built prod's `tracks` table. Also gone, and so is its
  successor `scripts/_sync_local_to_turso.py` (retired 2026-10-15, 1.12).

### 4.2 Stale docstrings that describe a pipeline that no longer exists
**verified:**

- ~~`backend/app.py:3068` — `/api/ingest/single` claims it "Reuses the
  `_process_track` pipeline (Modal ML → ...)".~~ Fixed 2026-10-17 (1.13).
- ~~`scripts/run_ingest_v2.py:9-17` — the module docstring lists four "wired
  stages" and says "youtube is independent".~~ Fixed before 2026-10-15 (it
  lists all seven); the last "all four stages" in its usage went 2026-10-15.
- `README.md` § "Arming, and the `done` invariant" — right after "There is
  no promote pass", a paragraph still says "Promote also **cascades**
  audio-availability failures downstream" (`no_match` copied into the
  download, ml, language and `embedding_status` columns). Nothing cascades
  since arming replaced promote: a `no_match` arms nothing, and preview and
  download settle the track at `'no_preview'` themselves (`arms` and
  `finalizes` in `ingest_pipeline/base.py`, read 2026-10-15). Delete the
  paragraph.

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
`backend/services/ingest_jobs.py` (was `backend/app.py:2814`)

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

### 4.10 Docstrings that claim a scope the code does not have
**verified** (2026-10-25, while characterising the routes for the refactor):

- `ingest_cancel` says "post-ingest z-score still runs" after a cancel.
  Nothing runs it: the import jobs stopped recomputing on 2026-10-13 (1.1).
- `get_track_features` says "Per-user"; it reads any catalogue track for
  any session (pinned in `tests/backend/test_meta_routes.py`). The streams
  are not scoped either, which is the shared catalogue working as intended.
- `/api/stream/{track_key}` takes a spotify_id only, unlike every other
  `{track_key}` route, which also accepts a `tracks.id` (pinned in
  `tests/backend/test_stream_routes.py`). Rename the parameter or resolve
  through `_resolve_anchor`.

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

Left in place, worth doing: delete the two `user_tracks` columns. (The
dedupe now moves `track_events` and rebuilds `user_track_stats`:
`scripts/_dedupe_tracks.py`, 1.13.)

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

## 10. Concurrent pipeline runs — built 2026-10-16, what is left

`scripts/run_ingest_v2.py --concurrent`: every selected stage in its own
thread with its own connection, each through its own gate, hand-offs by
event, a 5 s to 60 s back-off, rows in two gates to the feeding stage first
("Concurrent mode" in the runner's docstring; `tests/ingest/test_concurrent.py`).
The sequential runner is unchanged and stays the default.

### 10.1 Never run against Turso
`scripts/run_ingest_v2.py` `_ConcurrentRun`, `backend/db_client.py`

**unverified.** Up to seven `_HttpConnection`s read and write one libsql
database at once. Whether libsql-server queues a write batch whose first
UPDATE finds the write lock taken (its BEGIN is deferred), or answers
SQLITE_BUSY at once, has not been seen. `base._is_transient` takes "busy" /
"locked" as transient, so such a batch is retried 2, 4 and 8 s apart and
then stops the run with nothing parked: safe, but a run that keeps hitting
it ends early. Cheapest check: the first concurrent run against production,
after a backup (the sequential runner's first prod run went through on
2026-10-16, 1.12; a concurrent run is never small, since `--batch` does not
bound it), then `grep -E "retry [0-9] of 3|locked|busy"` in its log.

### 10.2 A concurrent drain reads 5 to 6 times the `tracks` rows a sequential one does
`scripts/run_ingest_v2.py` `_ConcurrentRun._work`, `FEEDER_WAIT_S`

**measured on a production-shaped SQLite file, 2026-10-16**: 6,238 tracks,
no indexes on `tracks` (as on production, 1.13), 400 pending rows that never
entered the pipeline, the real gates and writes, production's per-row
timings, every duration scaled by 1/10. A look is a scan of `tracks` there.
Rows read by a whole drain:

| `--batch` | sequential | concurrent, timer looks | concurrent, feeder wait |
|---|---|---|---|
| 50 | 116,764 | 1,450,576 | 714,492 |
| 10 | 515,996 | 3,952,014 | 2,766,794 |

Done since: a stage with a feeder still running no longer looks on the 5 to
60 s back-off but waits for the hand-off, with a 10-minute safety timer.
At `--batch 50` that took timer looks from 134 to 16 and looks from 223 to
105, with the drain still 14 minutes (sequential: 21). Idle, a
`--concurrent --loop` now looks 1.6 times a minute, ~10,000 rows at 6,238
tracks, against ~44,000 before. What is left is a look per stage after
each hand-off, often empty (fuse after every classify batch, its rows
waiting for a tag). The fix for that is indexes on the gate columns, which
belongs with 1.13's dedupe and unique index, or a hand-off that carries
its ids so the next stage reads them by key. `--until-drained` is still
what production should use.

## 11. Lyrics — built 2026-10-18, what is left

`GET /api/tracks/{key}/lyrics` (`backend/app.py` `get_track_lyrics`,
`backend/lyrics.py`), cached per track in `track_lyrics`
(`tests/backend/test_lyrics.py`).

### 11.1 ~~`track_lyrics` must be created on Turso before deploy~~ — DONE (2026-10-18)
`schema.sql` (end), `scripts/_turso_create_track_lyrics.py`

**Run on prod 2026-10-18** (dry run, then `--apply`: "created
track_lyrics"), before the deploy that shipped the endpoint (revision
vibescape-00027). Kept for the record: `ensure_db()` no-ops on Turso.
Without the table the endpoint still answers (it logs a warning and skips
the cache), but asks LRCLIB on every request. Run
`python scripts/_turso_create_track_lyrics.py --target prod` (dry run),
then with `--apply`, before the deploy that ships the endpoint.

### 11.2 LRCLIB is unlicensed, crowd-sourced lyrics: switch source before going public
`backend/lyrics.py`

**verified (a licensing fact, not a code one).** LRCLIB is free and keyless,
and its entries are user uploads with no licence from the rights holders.
That is fine for a private app. Before VibeScape is public, move the
source to a licensed provider (Musixmatch or LyricFind). Keep the endpoint
and the table: `source` and `source_id` exist for this, and the response
carries `source` and `credit`, so a new provider is a new `lookup()` plus
whatever attribution its licence requires.

### 11.3 `_dedupe_tracks.py` does not know `track_lyrics`
`scripts/_dedupe_tracks.py`

**verified by reading.** Nothing enables `foreign_keys`, so the schema's
`ON DELETE CASCADE` never fires: a merged-away track leaves its lyrics row
behind. Harmless (a cache, keyed by an id nothing will look up again), but
the keeper does not inherit the loser's lyrics either and is fetched anew.
Add the table to the dedupe script's list if that ever costs anything.

### 11.4 `tests/frontend/lyricsProvider.test.jsx` is flaky
`tests/frontend/lyricsProvider.test.jsx`

**verified 2026-10-19 by running it three times on main, unchanged code:**
one run passed all 5 tests, one failed 2 ("the active line follows the
clock and the offset", "a preview plays plain lyrics"), one failed a third
("the YouTube path stays plain until the video length matches"). A
different test failing each run points at timing in the test, not at the
feature, which works in use (local preview and prod, 2026-10-18/19).
Likely a missing `await`/`act`, or real and fake timers racing. It makes
`python tests/run_all.py` fail at random, which teaches people to ignore
red. Fix by making the test wait properly; prove it with ~20 consecutive
green runs. Deferred by the owner, 2026-10-19.

## 12. DJ language — built 2026-10-19 (branch `backend/dj-language`), what is left

The fused space is now `fused_v2`, 841 wide, in its own column
`track_embeddings.fused_v2_embedding F32_BLOB(841)`: a 64-slot language
block with an append-only code table (every tagged language has a slot;
unknown codes share 'other'; untagged is all zeros), weighing 0.40 (was
0.20). Language and skips come from the taste vector alone: dj_replay's
avoid term is stronger (NEG_WEIGHT 1.0, M_STEP 0.5, cap 2.0). A separate
session language re-rank was built and measured, and lost
(`scripts/dj_eval/language_rerank.py`). Measured offline with
`scripts/dj_eval/replay_bench.py --final`; the PR has the tables.

### 12.1 ~~Production needs the migration before the deploy~~ — DONE (2026-10-19)
Prod was migrated and runs fused_v2 (revision 00028). The pre-migration
fallback (`_fused_v2_ready`) stays until 12.2's cleanup.

### 12.2 Drop `fused_embedding` once fused_v2 has run on prod — the rollback path until then
`schema.sql`, `scripts/refuse_vectors.py`

**verified.** fused_v1's 788-wide column is untouched by the migration so
the previous revision still works on a rollback. Nothing in the new code
reads it, but `scripts/backup_prod_db.py` (its blob check),
`scripts/_turso_verify.py`, `scripts/_turso_create_vector_index.py`,
`scripts/dj_eval/setup_copy.py` and `groups.py` still name it. The cleanup:
point those at `fused_v2_embedding`, then `ALTER TABLE track_embeddings DROP
COLUMN fused_embedding` (SQLite 3.35+ / libSQL; no index or view uses it).
Until then a fuse from a checkout that has not pulled writes only the old
column and the track stays out of the DJ pool; re-running the migration
fills it.

### 12.3 ~~Two skips of one sound take its language with it~~ — DONE (2026-10-20)
`backend/dj_replay.py` (AVOID_PERP "lang")

The avoid term no longer pushes against the language the taste holds: N's
language block loses its component along S's. Mood skips and skips of an
entirely different sound in the session's language both keep the language
(`test_mood_skips_inside_a_language_keep_the_language`,
`test_skips_of_another_sound_in_the_same_language_keep_the_language`),
while a session that has moved to Hindi still drops Kannada on Kannada
skips.

### 12.4 ~~A skipped track's other release can stay~~ — DONE (2026-10-20)
`backend/same_song.py`, `backend/app.py` `_dj_drop_repeats`

The DJ drops any candidate that is another release of a song in the
session's log or of the seed, and any second release of a song already in
the response, on both ranking paths; a skip of such a repeat no longer
feeds the avoid term. Same-song repeats in the served top 10 went 0.189 ->
0.000 of listener moments and 0.252 -> 0.000 of real-user ones
(`replay_bench.py --final4`). It is per session only: a different release
of a song played yesterday can still come back (that is the recency
re-rank's job, by track id).

### 12.5 What the language work saw for the out-of-scope items
`scripts/dj_eval/replay_bench.py`

- **Duplicates.** Near-duplicates of a track already seen this session are
  in the top 10 at 4.5% of listener moments with the shipped design, as in
  v1. Duplicates of a *skipped* track: 0.4% throughout.
- **Untagged / ambient.** Untagged tracks are 0.2% of the top 10 in
  tagged-language listener sessions (v1: 0.5%). The 19 binaural sleep-tone
  tracks are untagged, so an instrumental session still meets them: the
  eligibility gate is still needed.
- **Skips are most of the signal.** Real-user finished-vs-skipped AUC went
  0.61 -> 0.75 from the avoid term alone. Item-level skip negatives are the
  obvious next measurement.

### 12.6 The skip fix costs some real-user AUC; real-user replay and listener sessions pull opposite ways
`backend/dj_replay.py`, `scripts/dj_eval/replay_bench.py`

**verified offline (2026-10-20).** The angle-bounded avoid term that keeps
the taste's language fixes the five regressed listener sessions, the
skip-heavy scenarios and the off-target seed, and real-user
finished-vs-skipped AUC drops 0.746 -> 0.707 (block bootstrap
-0.064..-0.014). Protecting all of S instead cost 0.104. The real users'
logs are short skip runs where "skipped three songs" mostly does mean "not
this", and an unbounded avoid term ranked the next skip low; the listener
sessions show the same term overshooting once someone skips for mood.
Before tuning the avoid term again: real-user AUC rewards it
monotonically, the listener sessions punish it past a point, and the
benchmark reports both (`replay_bench.py --final3`). PR #21's scenario
"three Kannada skips after three Kannada songs clear Kannada" (`skip3`) no
longer holds and cannot together with "three mood skips keep the
language".

### 12.8 A search is 80% of the taste, so a chill session that searched lo-fi gets instrumentals
`backend/dj_replay.py` ETA_SEARCHED

**verified offline (2026-10-20, by the team lead).** english-chill-aef5e7
searched SYML, SYML, then a lo-fi track: the last search makes up ~80% of S
(ETA_SEARCHED = 0.8), and the top 20 was 0 English / 20 instrumental with
the instrumental slot and 0 / 17 without it -- not the slot's doing. Lower
ETA_SEARCHED, or weight a search by how many searches came before it;
measure on `replay_bench.py --final3` before changing it.

### 12.7 The instrumental slot is set by the "no language" verdict, which also covers uncallable titles
`ingest_pipeline/fused_vector.py` (INSTRUMENTAL, lang_code)

**suspected.** `language_tagging.tag(clear=True)` is documented as "an
instrumental, or a title that genuinely cannot be called", so a vocal track
whose language the tagger could not call gets the instrumental slot. Of the
235 verdict tracks on the 2026-10-20 copy a title sample was all
instrumental (EDM, film themes, drones). Cheap check: list the verdict
tracks whose titles do not look instrumental and listen to a few.

## 13. Discovery mode — built 2026-10-21 (branch `backend/discovery`), what is left

`discover` on `/api/tracks/{key}/similar` ranks the whole catalogue with the
DJ's own query, re-ranks and pool, and tags each track `in_library` /
`discover` (`backend/app.py:2566`). Off is unchanged.

### 13.1 The Turso SQL has only run on SQLite with stand-in vector functions

`backend/services/dj.py` `_similar_dj` — the discovery query (no `user_tracks` join, the stats
join bound by a parameter) is tested through the Turso-shaped harness
(`tests/backend/test_dj_discover.py`), which proves the SQL parses and binds
on SQLite, not on libSQL. If libSQL rejects it, the except at the Turso call
falls through to the numpy path, which on Turso reads every fused vector in
the catalogue per request. **Suspected-safe.** Cheap check after deploy: one
discovery request, `mode_used` must end `_turso_discover`, and a WARNING
`turso vector_distance_cos path failed` must not appear.

### 13.2 The query is centred on the library mean, not measured with discovery on

`backend/services/dj.py` `_dj_events_query` — mu is the caller's library mean, as with discovery
off. Candidates from outside the library can sit far from that mean, and
nothing has measured whether language@10 holds. **Suspected.** Check:
`replay_bench.py` with the pool widened to the catalogue; the pre-approved
fallback, if it leaks, is the global mean (`_dj_library_mean(conn, 0, ...)`).

## 14. DJ score — built 2026-10-19 (branch `backend/dj-score`), what is left

`scripts/dj_eval/score_dj.py` scores a DJ version from listener sessions on
the three vibe tests in `scenarios.json` and keeps `scoreboard.json`.

### 14.2 Steer tests are not built yet
`scripts/dj_eval/scenarios.json`, `scripts/dj_eval/score_dj.py`

**by decision (2026-10-19).** The suite measures only whether the DJ keeps a
vibe it was shown. Whether it follows a listener who changes vibe (a second
search partway through: how many picks until it follows, whether the old
vibe leaks back) is unmeasured. A design was built and set aside in PR #24's
history (commit d9f52fd): phase A 5 picks, phase B 10, a `feedback --vibe
A|B|other` tag for leak.

### 14.1 The scoreboard's commit is whatever `--commit` says
`scripts/dj_eval/score_dj.py` `main`, `scripts/dj_eval/serve.py`

**verified by reading.** Nothing records which code served a round:
`serve.py` does not note its checkout's HEAD and the session files hold only
`base_url`. A wrong `--commit` (the main checkout's HEAD instead of the
worktree under test) files the score under the wrong version, silently. Fix:
`serve.py` writes its HEAD and dirty flag next to the copy, `listener.py
start` copies it into the session, and `score_dj.py` refuses a round whose
sessions disagree with each other or with `--commit`.

## 15. Genre scores and fused_v3 — built 2026-10-23 (branch `feat/genre-tags`), what is left

Four `tracks` columns (`genre_scores` JSON slug -> 50|100, `genre_status`,
`genre_model_version`, `genre_tagged_at`), the append-only 17-genre
taxonomy (`ingest_pipeline/genre_taxonomy.py`), `GenreStage` (parks live
rows at `'pending'`, gates nothing), `genre_tagging.tag_many` and
`scripts/genre_tags.py --apply-file`. Procedure: `ingest_pipeline/README.md`
§ Tagging genres. Then the DJ vector: **fused_v3**, fused_v2's 841 dims plus
a 24-slot genre block (`fused_vector.py`, W_GENRE 0.10 taken from MERT, chosen 2026-10-25), in
its own column `track_embeddings.fused_v3_embedding F32_BLOB(865)`. The
fuse stage and `scripts/refuse_vectors.py` write v3 and v2; `tag_many`
re-arms fuse for a fused row whose scores changed; the app ranks v3 once
the column holds a vector and v2 until then (`_fused_v3_ready`);
`dj_replay` protects only the language block, so skips push against genre.
Measured with `scripts/dj_eval/replay_bench.py --w-genre`.

### 15.1 Production: the migration order
`scripts/_turso_add_genre_columns.py`, `scripts/refuse_vectors.py`, `deploy/cloud-run/deploy.ps1`

**verified by reading and the local run** (nothing run against production).
In this order, each after a backup:

1. `backup_prod_db.py`, then `_turso_add_genre_columns.py --target prod
   --apply`. Until then a default `--target prod` run stops at its first
   query (`no such column: genre_status`); `--stages` without `genre` runs.
2. `run_ingest_v2.py --target prod --stages genre --batch 1000
   --until-drained` once (15.2), then tag (15.3).
3. `refuse_vectors.py --target prod` (dry run), then `--apply`: adds
   `fused_v3_embedding`, writes every v3 vector (and any stale v2, see
   15.6), drops the cached means. It also runs before 1 and 2 (no genre
   columns: neutral genre blocks, and later tags re-arm fuse), but after
   them it writes the genres in one go. Until it has run, a fuse against
   production fails its write ("no column named fused_v3_embedding": the
   run stops, nothing parked, `tests/ingest/test_stage_writes.py`), so it
   comes before the next production drain that fuses anything.
4. Deploy, with approval. A deploy that lands earlier ranks fused_v2
   exactly as today (absent or empty v3 column,
   `tests/backend/test_dj_paths.py`), so the order protects the DJ, not
   the deploy; but only after step 3 does the DJ use genre.

Between 3's ALTER and its last batch the column is partly filled; an app
that already has this code would rank only the written rows for those
minutes. Deploying after 3, as above, avoids it.

### 15.2 The first normalisation touches the whole finished catalogue
`ingest_pipeline/stage_genre.py` `gate_sql`

**verified by reading and the local test.** The gate admits finished rows
(they all need scores), so on the first run after 15.1's step 1 roughly
every finished track on production (5,483 done on 2026-10-16, per the lead
session) is in genre's gate. Inside a default run at `--batch 50` that is
~110 passes whose cohorts are all genre's, each a scan of `tracks` per
stage. Run `--target prod --stages genre --batch 1000 --until-drained`
once first.

### 15.3 Tag the production catalogue
`scripts/genre_tags.py`

**by decision.** Every live track is to be scored: `--waiting --limit 50`,
score against `genre_taxonomy.DEFINITIONS`, write a JSON-lines file (with
`spotify_id`), `--apply-file F` then `--apply`. The local copy
(`data/genre_dev.db`, prod's 2026-10-20 backup) was fully tagged on
2026-10-23 by a separate session; those tags were not reviewed, and they
are keyed to that copy's ids, so re-apply them to production by
`spotify_id` only. Each tag on a fused row now re-arms fuse (the cascade):
on production that is ~5,300 re-fuses on the next drain, milliseconds
each, or run step 3 of 15.1 after tagging and they re-fuse to the same
bytes. The tag write needs the same local permission rule the language
tags use.

### 15.4 W_GENRE is a starting value: choose it with the listener runs
`ingest_pipeline/fused_vector.py` `W_GENRE`

**verified offline (2026-10-23)**, on the fully tagged copy above:
W_GENRE 0 / 0.10 / 0.15 / 0.20 / 0.25 gave genre@10 0.500 / 0.594 / 0.611
/ 0.625 / 0.642 (listener) and 0.350 / 0.378 / 0.389 / 0.399 / 0.415
(real users), real-user served AUC 0.711 / 0.711 / 0.706 / 0.696 / 0.687,
language x energy p@10 0.694 / 0.677 / 0.646 / 0.624 / 0.610; lang@10 and
the five regression sessions held at every weight (the table is in the
fused_vector docstring). Genre and the listener AUC rise with the weight,
real-user AUC and energy fit fall: the same tension as 12.6. The listener
sessions were recorded on another copy (top8 fidelity 0.71 here, not
~1.0), so they are the weaker half. Before shipping: the DJ score's three
vibe tests (`scripts/dj_eval/README.md`) on 0.10 and 0.15 at least, twice
each, on a copy migrated with this branch's `refuse_vectors.py`. Changing
W_GENRE is a FUSED_MODEL_VERSION bump and a re-run of
`refuse_vectors.py`, no width change.

### 15.5 `backend/CLAUDE.md` still describes fused_v2 as what the DJ ranks
`backend/CLAUDE.md` § DJ replay (gitignored, so not in this branch)

**verified by reading.** It says fused_v2 is 841 wide in
`fused_v2_embedding` and that the app falls back with `_fused_v2_ready`;
since this branch the DJ ranks fused_v3 (865, `fused_v3_embedding`) with
`_fused_v3_ready` falling back to v2, and the avoid term deliberately
pushes against genre. Update it when the branch merges: the file is local
to each checkout.

### 15.6 The 2026-10-20 copy's fused_v2 vectors lack the instrumental slot for 235 tracks
`ingest_pipeline/fused_vector.py` (INSTRUMENTAL), `scripts/refuse_vectors.py`

**verified on the copy, suspected on production.** `refuse_vectors.py`'s
dry run on `data/genre_dev.db` (a copy of prod's 2026-10-20 backup) found
235 fused_v2 vectors that differ from the recipe, all `language` NULL with
`language_status='done'` (the "no language" verdict) and all only in the
language block (max difference 0.404): stored without the `zxx` slot
(PR #23, 2026-10-20). Either the backup predates that re-fuse or production
never had it, in which case the instrumental slot is not live there and
film-BGM sessions behave as before it. Cheap check: `refuse_vectors.py
--target prod` (dry run, reads only) and look at `fused_v2 rewrite`. Step
3 of 15.1 rewrites them either way.

### 15.7 `replay_bench.py` ranked a repeated track from uninitialised memory — fixed, numbers moved
`scripts/dj_eval/replay_bench.py` `Ranker.pool`

**fixed 2026-10-23.** A real-user moment whose served track was the seed or
in the seen window (a repeat) is not in the ranked set, but its rank was
read from an `np.empty` array: garbage, different on each run, inside
real-user AUC. 19 of 3,579 moments on the copy. It now has no rank and the
moment is left out. Cosines are also ranked rounded to 1e-9, so float noise
cannot order a tie. Real-user AUC from earlier runs (12.6's 0.746 -> 0.707,
for one) carried this noise; re-run `--final3` before comparing a new
number against them.

### 15.8 Track payloads carry `genres`; production answers `[]` until 15.1 step 1
`backend/app.py` `_track_select`, `_genre_scores_ready`, `backend/track_genres.py`

**verified by the local test** (`tests/backend/test_track_genres.py`; nothing
run against production). Every track dict (`_row_to_dict`) has `genres`, a
list of `{slug, score}` sorted by score then taxonomy order, `[]` when
untagged. The player's SELECTs name `genre_scores` only once a zero-row
`SELECT genre_scores FROM tracks LIMIT 0` succeeds (cached for the process;
a no is re-asked every 60 s and warned once), so a deploy before
`_turso_add_genre_columns.py` serves `genres: []` and no list breaks. The
raw `genre_scores` is not in the payload. `track_genres.GENRE_ORDER` copies
the taxonomy because the image ships without `ingest_pipeline/`; the test
fails when they or frontend-next's label map (`src/lib/genres.js`) drift,
so appending a genre means touching all three.

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
  `_dedupe_tracks.py`, `_turso_create_track_indexes.py`, `_fix_language_tags.py`, `_llm_verify_apply.py`,
  `language_tags.py`, `genre_tags.py` and `_turso_add_genre_columns.py` all require an explicit `--apply` and print
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
