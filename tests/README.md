# Tests

One test tree for the whole repo.

```
tests/
  conftest.py        shared fixtures: a fresh SQLite DB per test, users, tracks, embeddings
  backend/           FastAPI endpoints and pure backend modules (pytest)
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
imported, and asserts the app opened the test file. **Nothing here can reach
`data/vibescape.db` or production.** External calls (Spotify) are replaced
with fakes; no test needs a network.

## What is covered

| Area | File | Pins down |
|---|---|---|
| DJ replay maths | `backend/test_dj_replay.py` | search jumps, skips build an avoid term that stays bounded, skip runs prime the next listen, repeats can't run away, centring |
| DJ endpoint | `backend/test_dj_endpoint.py` | the replay follows a search (the seed's own events count), the legacy path's anchor drop, cold start from the seed, a seed with no vector, per-user and global library mean |
| Listening events | `backend/test_events.py` | 202 for everything but auth, routing to `track_events` / `user_events`, `data` validation, only plays feed the stats, `listened_ms` and its fallback, the 90 s rule, skips by any trigger, `abandoned`, u_/s_ split |
| Stats | `backend/test_me_stats.py` | totals, rates, top lists, local hours by timezone, windows, isolation between users |
| Spotify refresh | `backend/test_spotify_refresh.py` | session required, secret sent only server-side, rotation passed through, refusal is a 400 |
| Schema | `database/test_schema.py` | applies and re-applies, no `;` in comments breaks the Turso scripts' split (commit 322c7c1), fresh bootstrap survives many connections and keeps the production shape, old telemetry tables are widened in place |
| Cache = rebuild | `database/test_rollup_rebuild.py` | randomised sessions: the inline `user_track_stats` equals `scripts/rebuild_user_track_stats.py` |
| DJ event log | `frontend/dj.test.js` | append-only log, cap, v3 migration, request body |
| Listening log | `frontend/listenLog.test.js` | audible time excludes pauses and seeks, envelope, triggers, killed-tab recovery, a live tab is never recovered, sessions |
| Spotify tokens | `frontend/spotifyTokens.test.js` | refresh near expiry, PKCE vs server, single flight, refusal vs network failure, another tab's rotation |
| Spotify playback | `frontend/spotifyMedia.test.js` | a new song refreshes, a 401 forces one refresh and retry, the SDK callback gets a fresh token |

Each fix above was checked by breaking it on purpose and confirming a test
fails (2026-10-10).

## Not covered yet

React components (no DOM test environment is set up), the Turso transport
(`db_client` over Hrana), the offline ingest pipeline, and the Flutter app.

## Writing a test

Python: use the fixtures in `conftest.py` — `client` (FastAPI TestClient),
`make_user()` (returns `user_id, auth_headers`), `make_tracks(user_id,
vectors)`, `sql` (a raw connection to the test DB), `clusters` (synthetic
embedding groups). Frontend: no bare `import ... from 'vitest'` (files here sit
outside the package) — `describe` / `it` / `expect` / `vi` are globals, and
`tests/frontend/setup.js` gives each test a fresh `localStorage`, `window`
and `document`.
