# Tests

One test tree for the whole repo.

```
tests/
  conftest.py        shared fixtures: a fresh SQLite DB per test, users, tracks, embeddings
  backend/           FastAPI endpoints and pure backend modules (pytest)
  ingest/            pure ingest_pipeline modules (pytest)
  database/          schema.sql, migrations, the stats cache vs its rebuild (pytest)
  dj_real/           the DJ over real HTTP on a copy of production data (pytest; skipped by default)
  frontend/          frontend-next modules (vitest, config in frontend-next/vitest.config.js)
  run_all.py         runs everything, non-zero exit on any failure
```

## Running

```
pip install -r requirements.txt -r requirements-dev.txt   # once
npm --prefix frontend-next install                        # once

python tests/run_all.py                 # everything
python -m pytest tests                  # backend + database
python -m pytest tests/backend/test_events.py -k listened
npm --prefix frontend-next test         # frontend
```

## What the Python tests can and cannot touch

Every Python test runs against its own empty SQLite file in pytest's temp
directory, bootstrapped by the app's real `ensure_db()`. `conftest.py` forces
`DB_BACKEND=sqlite`, removes the Turso variables before the backend is
imported, points `--target prod`'s `.env.prod` at a file that does not
exist, makes `db_client`'s only way out to Turso (`_HttpConnection._post`)
raise unless a test gave its connection a fake one, and asserts the app
opened the test file. **Nothing here can reach `data/vibescape.db` or
production.** External calls (Spotify) are replaced with fakes; no test
needs a network.

Do not run a new test against old code when the test calls a script that
can reach production: the old code may get credentials some way the guards
do not cover. On 2026-10-15 a check of the backup test against the previous
commit ran the old `backup_prod_db.main()`, which read the token from
`scripts/_load_gcp_secrets.ps1`, before the `_post` guard existed.

## What is covered

| Area | File | Pins down |
|---|---|---|
| DJ replay maths | `backend/test_dj_replay.py` | search jumps, skips build an avoid term that stays bounded, skip runs prime the next listen, repeats can't run away, centring |
| DJ endpoint | `backend/test_dj_endpoint.py` | the replay follows a search (the seed's own events count), the legacy path's anchor drop, cold start from the seed, a seed with no vector, per-user and global library mean |
| DJ language and skips | `backend/test_dj_paths.py`, `ingest/test_fused_vector.py`, `backend/test_dj_replay.py` | end to end with vectors from the real fused recipe over shared sounds (only the language block tells Kannada from Hindi): a 70% Kannada library playing Hindi gets Hindi, so does a cold start on a Hindi seed, Kannada skips after the session moved to Hindi remove Kannada, mood skips inside a language keep it, so do skips of a wholly different sound in the session's language (fails unless only the language block is protected), an off-target seed is left when its vibe is skipped and the language stays (indian-heartbreak-9c8502's shape; fails if all of S is protected, or none), one stray skip does not flip the language, a switch to Hindi is followed within two songs; **the numpy path and the Turso `vector_distance_cos` path return the same order and scores**, with and without the recency re-rank (the Turso SQL runs on SQLite with libSQL's vector functions stood in for); the app, `db.py`, the schema and `fused_vector.py` agree on version, width and column for fused_v3 (865) and fused_v2 (841); fused_v2: 64 slots, the code table is append-only (a frozen prefix) and appending moves no vector, every tagged language has its own slot, an unknown code goes to 'other', an untagged track's language block is all zeros and equally far from every language, the "no language" verdict (NULL, status done) gets the instrumental slot `zxx` at INSTRUMENTAL_WEIGHT while a track with no verdict stays all zeros, no `km`. The avoid term never turns the query more than atan(AVOID_CAP) from the taste (`test_dj_replay.py`; fails without the cap or without the angle bound), and its language block is the recipe's. The language block's weight (0.40) and the instrumental slot's (0.5) have no end-to-end test that fails without them -- synthetic sounds separate too easily -- their evidence is `scripts/dj_eval/replay_bench.py` |
| DJ same-song filter | `backend/test_same_song.py` | `same_song.song_key`: album / single / remix / live / `(From "X")` releases share a key, Indic titles too; a dubbed song in another language is two songs; no usable title (the `?` rows, an all-Arabic title) is no key; a skip of another release of a song already in the log moves neither the query nor the skip streak; the DJ does not serve another release of a played song, nor two releases in one response; the response still has `limit` tracks with the recency re-rank on and off; the numpy and Turso paths filter alike |
| fused_v2 / fused_v3 migration | `database/test_refuse_vectors.py`, `database/test_schema.py` | `scripts/refuse_vectors.py` on a production-shaped table (no fused_v2 or fused_v3 column): dry run changes nothing; `--apply` adds `fused_v2_embedding F32_BLOB(841)` and `fused_v3_embedding F32_BLOB(865)` in one batch, writes both of the stage's own recipes (v3 with the row's genre block, neutral when untagged) in `execute_batch` chunks beside the untouched fused_v1 column, drops every cached mean; on production's current shape (v2 filled, no v3) it adds only the v3 column and writes only v3; a changed genre rewrites only that row's v3; a database without the genre columns gets neutral genre blocks instead of an error; a stale-encoder row gets no vector; a second run writes nothing; a pipeline from before fused_v2 fusing after the migration touches only the old column and the next run fills the new one; the end check counts widths with `LENGTH()`; `--target prod` exits without `.env.prod`. A local SQLite file from before fused_v2 gains both columns from `db.py` and keeps its v1 vectors |
| fused_v3 genre block and its fallback | `ingest/test_fused_vector.py`, `backend/test_dj_paths.py`, `backend/test_dj_replay.py`, `ingest/test_stage_writes.py` | slot i is `GENRES[i]`, 7 spare slots stay zero; scores / 100, L2'd within the block (one genre or three weigh the same); untagged (status not done, no scores, unknown slug, unreadable JSON) is all zeros and equally far from every genre; W_GENRE = 0 is fused_v2 bit for bit; the genre weight comes out of MERT's; a shared genre closes the gap by W_GENRE^2 over the squared weights; appending a genre moves no vector. The app ranks fused_v2 (its column, width, mean-cache key) while `fused_v3_embedding` is absent or empty, warns once, picks v3 up without a restart, and ranks v3 once it holds vectors; fused_v1 still when neither newer column exists. The avoid term protects the language block at both widths and pushes against the genre block. Fuse writes v3 and v2 from the row's genre scores; on a database without the v3 column it stops with nothing parked |
| DJ eval replay bench | `dj_real/test_replay_bench.py` | `replay_bench.py --w-genre 0,0.15` on a synthetic copy: G0 equals fused_v2 (AZ) on every number, G0.15 holds genre better, genre@10 is scored over tagged seeds only; primary genre = top score, ties to taxonomy order; a served track that was the seed or excluded has no rank (it read uninitialised memory). Rounding cosines to 1e-9 has no test that fails without it: it fixed a one-moment tie on the real copy |
| Listening events | `backend/test_events.py` | 202 for everything but auth, routing to `track_events` / `user_events`, `data` validation, only plays feed the stats, `listened_ms` and its fallback, the 90 s rule, skips by any trigger, `abandoned`, u_/s_ split |
| Stats | `backend/test_me_stats.py` | totals, rates, top lists, local hours by timezone, windows, isolation between users |
| Playlist import | `backend/test_playlist_import.py`, `backend/test_playlist_url_endpoint.py`, `frontend/playlistLink.test.js` | link detection for every service, each reader, YouTube title cleaning, matching, the Spotify budget (0 calls for Spotify links and catalogue hits, one search per song, pacing, stop on a long rate limit), the Amazon reader and its failure handling, Deezer refused, the job end to end, a new song stamped `created_at` on a `tracks` table with no column defaults (production's) |
| Route table | `backend/test_route_table.py` | every (methods, path) the app serves, in a fixed list, with its auth dependency (none / `require_user` / `require_admin` / `require_user_stream`, read by name from the dependency tree); no route twice; the SPA catch-all is last and `/next` precedes it; `/api/admin/*` is admin and only `/api/stream/*` takes `?token=`. The guard for the `app.py` split (`docs/backend-todo.md`, "Refactor in progress"); checked by switching one admin route to `require_user` in place |
| Auth routes | `backend/test_auth_routes.py` | email signup (normalised email, short display name and its suffix, scrypt hash, the admin-library seed, 409 / 422s), login (new session, `last_login_at`, one 401 for a wrong password, an unknown email or a Spotify-only account), logout ends only that session, `/api/auth/me`'s shape and its two 401s, Spotify OAuth sign-in (the exchange's form, the configured redirect fallback, a returning user keeps their name, the email-prefix fallback, 400 / 422 / 500s), spotify-link (stamps, refuses a guest and a taken id with HTTP 200 `ok: false`). Pinned as-is, marked ODD: the first account on an empty database is the admin, anyone named `Guest` is a guest |
| Admin routes | `backend/test_admin_routes.py` | users list, a user's stats, a user's tracks (newest first, `user_track_stats` play count, `last_played` = MAX(last played, last skipped), paging bounds), delete (user, links and sessions go, tracks stay, the admin and an unknown id are refused); 403 for a user, 401 without a session. ODD: a deleted user's stats stay behind on SQLite (backlog 3.14) |
| Small routes | `backend/test_meta_routes.py` | health, moods, demo/moods (nearest the band centre, `vibe_score_ml` first, artwork and `done` required, empty bands; ODD: a real 0 reads as 10), client-config, spotify/config (never the secret), the `/callback` page's `code` -> `spotify_code` rename and fixed return paths (checked by renaming it in place), track/{apple_id}/spotify (the caller's library only), track features by spotify_id or id (ODD: not per-user) |
| Streams | `backend/test_stream_routes.py` | both stream routes: full body and headers, mp3 vs mp4 type, Range 206 (closed, open, suffix), 416 with `Content-Range: bytes */size` or `invalid range`, `?token=` in place of the header and the header first, 401s, 404s for no row, no path, a missing file and a real file outside the project root. ODD: `/api/stream/{key}` takes a spotify_id only |
| YouTube lookup | `backend/test_youtube_routes.py` | GET returns the cached id or null (404, 422, 401); search passes the trimmed query and a limit clamped to 1-10, 400s for an empty or 300+ query, checks the track first, and answers `results: []` when the search fails or times out. yt-dlp never runs |
| Library sync and playlist jobs | `backend/test_ingest_jobs.py` | `/api/spotify/library` (counts, owner check, only the caller's own empty-count playlists resolved, every one when `/me` fails, 401 / 500; ODD: without the Spotify header the session token goes to Spotify, backlog 3.13). `/api/ingest/spotify` end to end on a real thread: dedup across sources, the four buckets, source labels, a failing track is skipped, cancel stops at the next track, and every failure (`/me` 401, mid-stream 401 and API error, an exception from the fetch, a worker that cannot connect) ends in `status: error`. `/api/ingest/spotify-public`: 15 preflight refusals with no job created, network errors as 502, the follow-then-reprobe note, user vs app token, job failures. DELETE `/api/ingest/status/{id}`: flags only a live job, 404 for another user's. Checked by removing the worker's error write in place |
| YouTube pin | `backend/test_track_youtube_auth.py` | `POST /api/tracks/{id}/youtube` writes the global `tracks.youtube_id`: admin only, a user or a guest session is a 403 and writes nothing, no session is a 401 |
| Spotify refresh | `backend/test_spotify_refresh.py` | session required, secret sent only server-side, rotation passed through, refusal is a 400 |
| Library search | `backend/test_library_search.py` | typos and partial words, language by name, every word required across fields, own songs above features, phrase above scattered words, only the caller's analysed tracks |
| Library heart | `backend/test_library_heart.py` | `PUT` / `DELETE /api/me/library/{key}` by id or spotify_id, idempotent, a PUT keeps an existing link's source and date, a DELETE takes only the caller's row and keeps the catalogue, embeddings, events and stats, 404 / 401; every player list carries `in_library`; an unlinked track leaves the DJ, search and the lists on the next request and comes back on a PUT; search drops a track another instance unlinked |
| Lyrics | `backend/test_lyrics.py` | the LRC parser (2- and 3-digit fractions, several timestamps on a line, `[offset:]`, blank lines kept as gaps, metadata and word stamps dropped); `/api/get` within 3 s is used with no search; search takes the closest synced entry within 3 s; one 10 s off is plain, never synced; a different title, artist, live or karaoke recording is never returned; one 503 is retried, two are `error: upstream` (HTTP 200, cached 10 minutes); User-Agent and 5 s timeout; the exact response; a cache hit makes no call; a `none` is re-asked after 30 days, not before; one `execute_batch`; served uncached without the table; auth; the one-shot script is a dry run by default. LRCLIB is never reached: `conftest.py` refuses `lyrics._http` |
| Spotify rate-limit guard | `backend/test_spotify_guard.py` | one 429 holds every later call with no request, counts down and lifts, never shortens, library/matcher/importer fail fast instead of sleeping, `/api/spotify/status` |
| Score recompute | `backend/test_recompute_scores.py`, `ingest/test_runner_recompute.py` | same values as the function it replaced, unchanged rows are not rewritten, writes go out in `execute_batch` chunks, the endpoint is admin-only and global, an import job leaves catalogue scores alone, the runner recomputes only after librosa or classify finished a row, `--loop` recomputes once when it drains (also before an `--only-ids` exit) and retries a failed one at the next idle, `--until-drained` recomputes once and exits (1 if that failed) without sleeping, an idle `--loop` backs off 30 s to 10 min and work resets it, `--recompute-only` |
| iTunes preview matcher | `ingest/test_preview_matcher.py` | an album neighbour is never the song, accents and ` - ` / `(From ...)` suffixes, a shared film name is not a match, 8D / slowed / reprise are different recordings, artist overlap, length tolerance |
| Runner cohort | `ingest/test_cohort.py` | with the real language and fuse stages: a second pass advances the next ids instead of re-selecting rows waiting for a tag, the loop drains only when no stage has work, a tagged row fuses while lower ids still wait, only the selected stages count, `--only-ids` |
| Gates | `ingest/test_gates.py` | each of the eight real stages' gates accepts exactly its row; the cohort is one ids-only query ORing them; the startup counts are per gate (a row waiting for its tag counts nowhere); an empty cohort matches nothing |
| `--concurrent` | `ingest/test_concurrent.py` | with real `Stage` subclasses on the test's SQLite file: the next stage works through its first batch while the first is inside its second; a hand-off wakes a stage within a second, not at its 600 s safety timer; `--until-drained` waits out a slow upstream whose output arrives batch by batch, then recomputes once, only if a scoring stage finished rows (exit 1 if the recompute fails); a stage in a batch is not idle; a finished track left armed does not keep a concurrent run busy, and a pass and a stage thread count a batch with the same helper; `--loop` recomputes per drain, not per batch, and retries a failed one at the next idle look; a single pass takes what the gates hold and what feeders hand on, not what reaches a stage that has stopped, and a stage whose feeder stops without handing off finishes at once; who feeds whom, nothing across a `--stages` gap, a stage given twice or a circle refused; a row in two gates goes to its feeder, which holds only because `_look` hands `run_batch` just the ids it found; on a 4,374-state matrix no stage's look shares a row with any stage upstream of it, feeders followed all the way up (with direct feeders only, five pairs did); language waits for preview and download, so a no-preview row keeps NULL; genre waits for the same two and is upstream of nothing; classify's gate leaves a row librosa can still act on, in a pass and in a concurrent run; a feeder gate that is NULL for a row does not hide it; an idle look is one ids-only query for every real gate, and for one stage it is a pass's cohort query word for word; a failing stage stops the others after their batch (exit 1, bounded), and so does one that cannot connect; Ctrl-C stops every stage after its batch (130), even with a stage thread dead, and is logged even when the last stage ends in the same tick, and a second exits at once; a stop that lands just before a stage clears its event is not lost, and one that lands during its look starts no batch; a stage whose feeder still runs waits for its hand-off, not on a timer, while one with no feeder running keeps the back-off, 5 s to 60 s, and its reset; two stage threads write at once and their first commits wait out a read another connection holds open, with no lock error, and the file stays in rollback-journal mode; `--only-ids` through `main()`; the summary line; every line names its stage, from inside a batch too. A watchdog presses Ctrl-C after 30 s so no run can hang the suite |
| ffmpeg children | `ingest/test_ffmpeg_children.py` | the ffmpeg that decodes a row's audio for librosa (`ingest/features.py`) and classify (`ml/src/predict.py`) starts in a process group of its own (Windows) or a session (POSIX), so a console Ctrl-C cannot kill it mid-decode; `subprocess.run` mocked, predict's loader run from its source (importing the module takes 47 s) |
| Read retries | `ingest/test_read_retries.py`, `ingest/test_concurrent.py` | a stage's fetch, a pass's cohort query and its startup counts, and a concurrent look are retried like writes: a dropped connection twice, then rows, costs pauses 2 and 4 s, each logged, and the batch or the run goes on; one that keeps failing is raised after its third retry (a concurrent run then exits 1); a read error that is not transient is raised at once |
| Stage writes | `ingest/test_stage_writes.py` | a clean batch is one `execute_batch`; a row that violates a constraint is set aside, costs one more batch, and is parked at `<stage>_stage_error` while the rest land; a schema error raises with nothing parked even when only one row writes the column; a connection error, a timeout or a lock is tried again three times, 2, 4 and 8 s apart, and the batch lands if a retry does, else it raises with nothing parked (a transport error logged "outcome unknown"); an unowned error or a schema error raises at once; a set-aside row's own write is retried too, and a retry after an attempt that did commit lands the same, parks included; the UNIQUE retry drops the droppable fields, also for two collisions side by side; a finished track is never parked: a re-fuse whose write keeps failing, or that fails, leaves it `'done'` and armed (counted `left_armed`) while an in-flight row beside it is parked, and a pass that only left finished tracks armed is no progress for the loop; classify writes its vector in the row's transaction, and fails a row whose model output (a score or the vector) is NaN or infinite; fuse never marks a row done without its vector |
| Genre scores | `ingest/test_genre_tags.py` | the taxonomy keeps its first 17 slugs as a frozen prefix (append-only), no duplicates, a version, one-line definitions; a score is a non-empty object of known slugs at 50 or 100 (0, 75, a string, `True`, `100.0`, `{}` refused) and is stored compact in taxonomy order; `tag_many` writes every row in one `execute_batch`, validates all before writing (one bad score or a repeated id writes nothing), lands nothing when a statement fails inside the batch, and reports ids that match no row or not their `spotify_id`; in the same statement it re-arms fuse for a fused row whose scores changed or whose status was not done, never for unchanged scores, an unfused row or a parked one; `fetch_waiting` returns exactly its columns, never the iTunes `genre`; `GenreStage` normalises NULL to pending for live rows (pending, NULL and done) and leaves `no_preview`, untitled and tagged rows, writes nothing but its column; fuse and youtube take a row whatever its `genre_status`, genre arms nothing and no stage comes after it; genre is in the default stages; `genre_tags.py`: a dry run writes nothing, `--apply` writes 200 rows a batch, one bad line refuses the whole file, `--status` exits 1 while rows wait, `--waiting` prints exactly the waiting columns; a local file without the columns gains them from `db.py`; `_turso_add_genre_columns.py` is a dry run by default and idempotent |
| Genre tags in payloads and the UI | `backend/test_track_genres.py`, `frontend/genres.test.jsx` | every player list (`/api/tracks`, random, search, `/similar` GET, DJ with and without a session log, an already-ingested single) carries `genres`, score then taxonomy order, `[]` untagged, never the raw `genre_scores`; unreadable JSON, unknown slugs and scores are dropped; on a database without the column (production's shape, SQLite and the Turso DJ SQL) every list answers with `genres: []`, warns once, and picks the column up without a restart; the check is cached; the backend's order equals the taxonomy and the frontend label map has every slug in order. Frontend: the 17 labels, chip order, now-playing chips (primary vs secondary) and the iTunes-text fallback, one chip on a queue and a library search row, none on a Spotify one. The 360px container query and the hidden-on-phone rule are CSS only, unchecked |
| `--retry-failed` | `ingest/test_retry_failed.py` | one stage's parked rows are re-armed (its own column, ingestion_status, error cleared) and nothing else; `all`; the flag logs the count, then runs; an unknown stage is refused |
| `--target` | `ingest/test_target.py` | local forces SQLite whatever `DB_BACKEND` says; prod loads `.env.prod` over `.env`, needs all three keys in that file and `DB_BACKEND=turso`, never prints the token; the runner, `language_tags.py`, `genre_tags.py` and `backup_prod_db.py` exit before connecting without the file; the runner and both tag CLIs start under `python -P`. Every test points `.env.prod` at a missing file (`conftest.py`) |
| Turso adapter | `database/test_turso_adapter.py` | against a fake Hrana server running on in-memory SQLite: blobs padded / unpadded / url-safe and the two shapes production sends, a 788-float vector round trip, an undecodable blob raises, `SQLITE_CONSTRAINT*` is `IntegrityError`, a batch is one request with the BEGIN / conditional / COMMIT / ROLLBACK steps, a failing statement lands nothing and names its step (`step`, `detail`), `rowcount` 0 is 0, `commit()` / `rollback()` send nothing, a batch waits 120 s and a statement 30 s, NaN and infinities go as NULL so a request stays valid JSON, no test can reach a real database; the SQLite `execute_batch` is all-or-nothing, names its step, and rolls back a COMMIT that fails (a reader holding the lock) instead of leaving the transaction open; every SQLite connection waits 30 s for a lock, not Python's 5 s |
| Schema | `database/test_schema.py` | applies and re-applies, no `;` in comments breaks the Turso scripts' split (commit 322c7c1), fresh bootstrap survives many connections and keeps the production shape, old telemetry tables are widened in place |
| Track dedupe | `database/test_dedupe_tracks.py` | `scripts/_dedupe_tracks.py` on a prod-shaped file (no tracks indexes): spotify_id and ISRC merge transitively, keeper `done` then lowest id, apple_id is cleared rather than merged, a user holding both rows keeps one link (earliest `added_at`) and one holding only a loser is re-pointed, events move, the stats cache equals a rebuild, embeddings go with their rows, one `execute_batch` per group and a failing group lands nothing, a dry run writes nothing, `--target prod` exits without `.env.prod`; `_turso_create_track_indexes.py` refuses while duplicates remain, then builds all four once; a bootstrapped prod copy with its dead `tracks.user_id` and a unique index keeps its shape |
| DJ eval harness | `dj_real/test_dj_eval_groups.py` | the eval's primary-artist and album / film keys, energy terciles, and its copy of the frontend's played-ratio thresholds |
| DJ score | `dj_real/test_score_dj.py` | `scripts/dj_eval/score_dj.py` vibe tests, on synthetic sessions: hold, skip rate, skip recovery (the pick right after a skip; none without one, and then left out of the score, not 0 or 1), early (picks 1-3), the 50/25/15/10 weights, the DJ score as the mean over scenarios. Validation: an up-next play or a second search inside the 15, too few plays, another start song, a skipped start song, an unrated pick or the wrong account exclude the run; plays past the last step are ignored and the run scores on its first 15. The run-to-run spread; a single-run entry is provisional and never the best; the best is per scenario version; `NEW BEST` / `Δ vs best`, within / outside the spread; the same label twice is refused unless `--replace`, which recomputes the best; stored prompts equal the template; no two scenarios share an account. 25 rules were each disabled in place and a test failed (2026-10-19) |
| DJ, end to end | `dj_real/test_dj_real.py` | **skipped unless `DJ_EVAL_DB` or `DJ_EVAL_BASE_URL` is set** (below): a real uvicorn on a copy of production, signed in through `/api/auth/login`, the request the frontend sends; every call 200, no duplicates, nothing already played, skipped or seeded comes back, through a 10-step autoplay loop |
| DJ listener CLI | `dj_real/test_listener_cli.py` | a track prints as handle, title, artist, album and nothing else; the state file holds no language or vibe; **with `DJ_EVAL_DB`**, a scripted search / play / next / skip / feedback / review session against a real server prints exactly the database's title, artist and album for every handle, no spotify id, and no hidden-field word |
| Cache = rebuild | `database/test_rollup_rebuild.py` | randomised sessions: the inline `user_track_stats` equals `scripts/rebuild_user_track_stats.py` |
| Serving the SPA after a deploy | `backend/test_spa_serving.py`, `frontend/chunkReload.test.js` | `/assets/*` is `public, max-age=31536000, immutable`; index.html, the client-route fallback and other unhashed files are `no-cache` and answer a matching `If-None-Match` / `If-Modified-Since` with 304; a missing asset or extensioned file is a 404 (`no-store`), never index.html; `/api` is not swallowed. The client reloads once on a chunk-load error, refuses a second within 60 s, reloads again after it, never without sessionStorage, holds a lazy import while reloading and renders nothing when refused; `vite:preloadError` reloads without being cancelled |
| Sync job | `frontend/syncJobProvider.test.jsx` | a job `note` becomes a toast instead of crashing the poll effect (the real `ToastProvider`, only the API and the player mocked), once per job |
| DJ event log | `frontend/dj.test.js` | append-only log, cap, v3 migration, request body |
| Listening log | `frontend/listenLog.test.js` | audible time excludes pauses and seeks, envelope, triggers, killed-tab recovery, a live tab is never recovered, sessions |
| Spotify tokens | `frontend/spotifyTokens.test.js` | refresh near expiry, PKCE vs server, single flight, refusal vs network failure, another tab's rotation |
| Spotify playback | `frontend/spotifyMedia.test.js` | a new song refreshes, a 401 forces one refresh and retry, the SDK callback gets a fresh token, a blocked play makes no request |
| Media session | `frontend/mediaSession.test.js` | next/previous registered and the +/-10 s seek pair not (lock screens show one pair), a lock-screen next reaches `onNext` as `media_key`, the session anchor is a >5 s silent WAV under 200 KB, each anchor loop re-pushes the song's position past the throttle |
| Spotify guard (browser) | `frontend/spotifyGuard.test.js` | held calls make no request, lifts on time, kept across reloads, never shortens |
| Accent | `frontend/accent.test.js` | accent text clears 4.6:1 on every surface and badge at every vibe in both themes, ink on the fill too, the solver's surfaces match `tokens.css`, track badges coloured by the track |
| Volume | `frontend/volume.test.js` | clamping, persistence, mute, subscribers, effective volume 0 while muted |
| DJ line | `frontend/startReason.test.js` | a finish vs a skip vs a slider move, a stale "ended" flag never relabels a search or pick |
| Vibe arrow keys | `frontend/vibeArrowKeys.test.jsx` | up / down fetch for the vibe they move to, clamped at 0 and 100, two presses before a render compound (the real `PlayerProvider` and shortcut hook) |
| Stale DJ pick | `frontend/djStalePick.test.jsx` | a DJ pick that lands after a user pick, or after a newer next, is dropped with no play_start / play_end and no random fallback; the pick ends the overlay; an unraced pick still plays (real `PlayerProvider`, `media/player.js` and listening log) |
| Preview fallback | `frontend/previewFallback.test.js` | after several previews load, one error falls back once to the current track's stream; never twice; a no-preview track and `stop()` leave nothing armed |

Each fix above was checked by breaking it on purpose and confirming a test
fails (2026-10-10). The cohort, stage-write, Turso-adapter, recompute-batch,
`created_at`, gate, `--retry-failed`, loop and `python -P` tests were run
against the code before their fix and fail there (2026-10-15). The
backup-refusal test was not, and must not be (see above). For
`--concurrent` (2026-10-16), each piece of the runner was disabled in
place, one at a time, and a test aimed at it failed: 30 in the first round
(the hand-off, the drain rule, stop on failure, the Ctrl-C handler,
precedence, COALESCE, ...; two of them, WAL and the stage threads' own lock
wait, are gone since), and 10 in the review round (db_client's 30 s lock
wait, WAL staying off, both stop checks, the shared batch counting, the
shared id query, classify's librosa clause, COALESCE again, the look's ids
and its exclusion). For fused_v3 (2026-10-23), 19 pieces were disabled in
place one at a time (the language block's widths, the v3 readiness and v2
fallback, the cascade and its status clause, fuse's v2 write and genre
read, refuse's v3 recipe and genre read, the untagged rule, the block's L2,
the weight's source, db.py's column, the bench's genre metric, v3 spaces
and rank fix, refuse's tolerance of missing genre columns): 17 failed a test. The two that did not are robustness, not
behaviour: fused_vector's block-wise norm (a plain L2 gave the same bytes
here) and replay_bench's 1e-9 rounding (it fixed one tied moment on the
real copy).

## The DJ against real data

`tests/dj_real/test_dj_real.py` and `test_listener_cli.py` run the DJ through
`POST /api/tracks/{key}/similar` on a copy of a production backup, never on
the backup itself and never on production (`scripts/dj_eval/README.md`):

```
python scripts/dj_eval/setup_copy.py --backup data/backups/prod_<stamp>.db   # -> data/dj_eval/eval.db
DJ_EVAL_DB=data/dj_eval/eval.db python -m pytest tests/dj_real               # basics only
```

Do not run them while `run_eval.py` is running on the same copy: both use
the first eval user and wipe its history. The copy runs on SQLite, so the DJ
ranks with the numpy path, not the `vector_distance_cos` SQL production uses
on Turso. The tests assert no quality floor; that is what the reports are for.

## Not covered yet

React components beyond `SyncJobProvider` and the `PlayerProvider` paths
named above; the Turso transport against a real server (the adapter tests
use a fake that follows the Hrana spec, so a server that departs from it
would not be caught); the network and GPU stages of the offline pipeline
(preview, download, librosa, the MERT pass itself, youtube); `--concurrent`
against Turso, where several threads would write at once; and the Flutter
app.

## Writing a test

Python: use the fixtures in `conftest.py` — `client` (FastAPI TestClient),
`make_user()` (returns `user_id, auth_headers`), `make_tracks(user_id,
vectors)`, `sql` (a raw connection to the test DB), `clusters` (synthetic
embedding groups), `use_conn(opener)` (every `db.get_conn()` returns
`opener()`; `use_conn.real` is the unpatched one). To open a connection the
way the app does, call `db.get_conn()` (imported as `backend_db`: `db` is the
fixture with the test database's path). `backend/app.py` has no `get_conn`
of its own, so `monkeypatch.setattr(app, "get_conn", ...)` raises rather
than patching a name nothing calls. Frontend: no bare `import ... from 'vitest'` (files here sit
outside the package) — `describe` / `it` / `expect` / `vi` are globals, and
`tests/frontend/setup.js` gives each test a fresh `localStorage`, `window`
and `document`.

A component test is a `.test.jsx` file that starts with
`// @vitest-environment jsdom`; it then gets a real DOM (`setup.js` leaves
it alone) and `@testing-library/react`. Bare imports of `react`,
`react-dom`, `@tanstack/react-query` and `@testing-library/react` resolve
through aliases in `vitest.config.js`; add any other package there.
