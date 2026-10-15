# Tests

One test tree for the whole repo.

```
tests/
  conftest.py        shared fixtures: a fresh SQLite DB per test, users, tracks, embeddings
  backend/           FastAPI endpoints and pure backend modules (pytest)
  ingest/            pure ingest_pipeline modules (pytest)
  database/          schema.sql, migrations, the stats cache vs its rebuild (pytest)
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
| Listening events | `backend/test_events.py` | 202 for everything but auth, routing to `track_events` / `user_events`, `data` validation, only plays feed the stats, `listened_ms` and its fallback, the 90 s rule, skips by any trigger, `abandoned`, u_/s_ split |
| Stats | `backend/test_me_stats.py` | totals, rates, top lists, local hours by timezone, windows, isolation between users |
| Playlist import | `backend/test_playlist_import.py`, `backend/test_playlist_url_endpoint.py`, `frontend/playlistLink.test.js` | link detection for every service, each reader, YouTube title cleaning, matching, the Spotify budget (0 calls for Spotify links and catalogue hits, one search per song, pacing, stop on a long rate limit), the Amazon reader and its failure handling, Deezer refused, the job end to end, a new song stamped `created_at` on a `tracks` table with no column defaults (production's) |
| YouTube pin | `backend/test_track_youtube_auth.py` | `POST /api/tracks/{id}/youtube` writes the global `tracks.youtube_id`: admin only, a user or a guest session is a 403 and writes nothing, no session is a 401 |
| Spotify refresh | `backend/test_spotify_refresh.py` | session required, secret sent only server-side, rotation passed through, refusal is a 400 |
| Library search | `backend/test_library_search.py` | typos and partial words, language by name, every word required across fields, own songs above features, phrase above scattered words, only the caller's analysed tracks |
| Spotify rate-limit guard | `backend/test_spotify_guard.py` | one 429 holds every later call with no request, counts down and lifts, never shortens, library/matcher/importer fail fast instead of sleeping, `/api/spotify/status` |
| Score recompute | `backend/test_recompute_scores.py`, `ingest/test_runner_recompute.py` | same values as the function it replaced, unchanged rows are not rewritten, writes go out in `execute_batch` chunks, the endpoint is admin-only and global, an import job leaves catalogue scores alone, the runner recomputes only after librosa or classify finished a row, `--loop` recomputes once when it drains (also before an `--only-ids` exit) and retries a failed one at the next idle, `--until-drained` recomputes once and exits (1 if that failed) without sleeping, an idle `--loop` backs off 30 s to 10 min and work resets it, `--recompute-only` |
| iTunes preview matcher | `ingest/test_preview_matcher.py` | an album neighbour is never the song, accents and ` - ` / `(From ...)` suffixes, a shared film name is not a match, 8D / slowed / reprise are different recordings, artist overlap, length tolerance |
| Runner cohort | `ingest/test_cohort.py` | with the real language and fuse stages: a second pass advances the next ids instead of re-selecting rows waiting for a tag, the loop drains only when no stage has work, a tagged row fuses while lower ids still wait, only the selected stages count, `--only-ids` |
| Gates | `ingest/test_gates.py` | each of the seven real stages' gates accepts exactly its row; the cohort is one ids-only query ORing them; the startup counts are per gate (a row waiting for its tag counts nowhere); an empty cohort matches nothing |
| Stage writes | `ingest/test_stage_writes.py` | a clean batch is one `execute_batch`; a row that violates a constraint is set aside, costs one more batch, and is parked at `<stage>_stage_error` while the rest land; a schema error raises with nothing parked even when only one row writes the column; a connection error, a timeout (logged "outcome unknown"), a lock or an unowned error raises with nothing parked; the UNIQUE retry drops the droppable fields, also for two collisions side by side; classify writes its vector in the row's transaction; fuse never marks a row done without its vector |
| `--retry-failed` | `ingest/test_retry_failed.py` | one stage's parked rows are re-armed (its own column, ingestion_status, error cleared) and nothing else; `all`; the flag logs the count, then runs; an unknown stage is refused |
| `--target` | `ingest/test_target.py` | local forces SQLite whatever `DB_BACKEND` says; prod loads `.env.prod` over `.env`, needs all three keys in that file and `DB_BACKEND=turso`, never prints the token; the runner, `language_tags.py` and `backup_prod_db.py` exit before connecting without the file; both CLIs start under `python -P`. Every test points `.env.prod` at a missing file (`conftest.py`) |
| Turso adapter | `database/test_turso_adapter.py` | against a fake Hrana server running on in-memory SQLite: blobs padded / unpadded / url-safe and the two shapes production sends, a 788-float vector round trip, an undecodable blob raises, `SQLITE_CONSTRAINT*` is `IntegrityError`, a batch is one request with the BEGIN / conditional / COMMIT / ROLLBACK steps, a failing statement lands nothing and names its step (`step`, `detail`), `rowcount` 0 is 0, `commit()` / `rollback()` send nothing, no test can reach a real database; the SQLite `execute_batch` is all-or-nothing and names its step |
| Schema | `database/test_schema.py` | applies and re-applies, no `;` in comments breaks the Turso scripts' split (commit 322c7c1), fresh bootstrap survives many connections and keeps the production shape, old telemetry tables are widened in place |
| Cache = rebuild | `database/test_rollup_rebuild.py` | randomised sessions: the inline `user_track_stats` equals `scripts/rebuild_user_track_stats.py` |
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
backup-refusal test was not, and must not be (see above).

## Not covered yet

React components beyond `SyncJobProvider` and the `PlayerProvider` paths
named above; the Turso transport against a real server (the adapter tests
use a fake that follows the Hrana spec, so a server that departs from it
would not be caught); the network and GPU stages of the offline pipeline
(preview, download, librosa, the MERT pass itself, youtube); and the
Flutter app.

## Writing a test

Python: use the fixtures in `conftest.py` — `client` (FastAPI TestClient),
`make_user()` (returns `user_id, auth_headers`), `make_tracks(user_id,
vectors)`, `sql` (a raw connection to the test DB), `clusters` (synthetic
embedding groups). Frontend: no bare `import ... from 'vitest'` (files here sit
outside the package) — `describe` / `it` / `expect` / `vi` are globals, and
`tests/frontend/setup.js` gives each test a fresh `localStorage`, `window`
and `document`.

A component test is a `.test.jsx` file that starts with
`// @vitest-environment jsdom`; it then gets a real DOM (`setup.js` leaves
it alone) and `@testing-library/react`. Bare imports of `react`,
`react-dom`, `@tanstack/react-query` and `@testing-library/react` resolve
through aliases in `vitest.config.js`; add any other package there.
