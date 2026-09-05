# VibeScape Backend API — Mobile Client Reference

Reference for building a mobile (iOS / Android / React Native / Flutter)
frontend against the VibeScape backend. Every endpoint below is
implemented in `backend/app.py` and lives behind a single FastAPI
process deployed on Google Cloud Run.

---

## 1. Hosting

| | |
|---|---|
| **Provider** | Google Cloud Run (region `us-central1`) |
| **Base URL** | `https://vibescape-241988497106.us-central1.run.app` |
| **Container** | Repo root `Dockerfile`, built by Cloud Build on `gcloud run deploy --source .` |
| **Runtime** | Python 3.13 + FastAPI + Uvicorn |
| **Scaling** | `min-instances=0`, `max-instances=3`, `timeout=300s`, cold-start ~1–3 s |
| **CPU / Memory** | 1 vCPU / 512 MiB |
| **Auth on service** | `--allow-unauthenticated` — endpoint is public; per-request auth is handled by the app (bearer session token) |
| **CORS** | `allow_origins=["*"]`, all methods, all headers — mobile clients on any origin are fine |
| **Data store** | Turso (libSQL) via `DB_BACKEND=turso`. Local dev falls back to SQLite at `data/vibescape.db` |
| **ML inference** | Modal (`VIBESCAPE_ML_MODE=modal`) — the Cloud Run VM never runs torch; it POSTs preview URLs to Modal T4 GPU functions |
| **Health check** | `GET /api/health` returns `{"status":"ok","track_count":N}` |
| **Deploy runbook** | `deploy/cloud-run/README.md` + `deploy/cloud-run/deploy.ps1` |

Secrets injected via GCP Secret Manager at runtime:
`SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `MODAL_TOKEN_ID`,
`MODAL_TOKEN_SECRET`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`.

---

## 2. Authentication model

Two independent auth surfaces the mobile client must handle:

### 2a. VibeScape session (required for almost every endpoint)

- Users are stored server-side with a `display_name` and optional 4-digit
  PIN (scrypt-hashed).
- Signup or login returns a **session token** — a 64-char hex string with
  no expiry, revoked only on explicit logout or admin delete.
- Send it as `Authorization: Bearer <session_token>` on every request.
- On the audio-stream endpoints (`/api/stream/...`) it may also be
  supplied as `?token=<session_token>` because native `<audio>` elements
  and some mobile media players cannot set custom headers. This
  fallback is stream-only.

### 2b. Spotify OAuth (only for Spotify-catalog + library-ingest calls)

- The backend does **not** hold Spotify tokens on the mobile user's
  behalf. The mobile app performs the OAuth Authorization Code flow with
  PKCE against Spotify directly using the `client_id` returned by
  `GET /api/spotify/config`.
- Once obtained, the mobile app sends the Spotify access token as
  `X-Spotify-Authorization: Bearer <spotify_access_token>` on the
  handful of endpoints that call Spotify on behalf of the user
  (`/api/spotify/library`, `/api/spotify/search`, `/api/ingest/spotify`,
  `/api/ingest/spotify-public`, `/api/ingest/single`).
- Tokens are used only for the lifetime of the request/job — never
  persisted on the server. Optionally, the mobile app can call
  `POST /api/auth/spotify-link` to associate the caller's Spotify user
  ID with their VibeScape profile (metadata only, no token stored).
- The stock redirect URI is
  `https://vibescape-241988497106.us-central1.run.app/callback` — a
  static HTML page that stashes the auth code in `localStorage` and
  closes the popup. For a native mobile app, register a **custom-scheme
  or Universal Link redirect** on the same Spotify Developer app and
  parse the `code`/`state` in-app instead.

---

## 3. Common conventions

- **Media type**: `application/json` for requests and responses unless
  noted (`/api/stream/*` returns audio bytes).
- **Errors**: FastAPI `HTTPException` returns `{"detail": ...}`. `detail`
  is either a string or an object shaped `{"error": "<code>", ...}`.
  Handle both.
- **Pagination**: most list endpoints take `limit` (bounded server-side)
  and, where applicable, `offset`.
- **Track keys**: three IDs coexist. `id` is the internal DB row id.
  `spotify_id` is the 22-char base62 Spotify track ID. `apple_id` is
  the numeric iTunes track ID. Endpoints that take a `{track_key}`
  path segment accept **either** `spotify_id` (string) or `apple_id`
  (numeric string) and resolve internally.
- **Vibe scores**: `vibe_score` (0–100, formula-based, always present)
  and `vibe_score_ml` (0–1, from the MERT model, nullable until the
  ML backend runs on the track). The frontend slider drives
  `COALESCE(vibe_score_ml * 100, vibe_score)`.
- **Moods**: string from the fixed set exposed at `GET /api/moods`.

---

## 4. Endpoint reference

### 4.1 Health + config (unauthenticated)

| Method | Path | Returns | Notes |
|---|---|---|---|
| GET | `/api/health` | `{status, track_count}` | Ping. |
| GET | `/api/moods` | `{moods: string[]}` | Fixed 2×5 mood-grid labels. |
| GET | `/api/client-config` | `{env, debug}` | Runtime env flag. |
| GET | `/api/spotify/config` | `{client_id, redirect_uri}` | Mobile app uses `client_id` for its own OAuth flow. |
| GET | `/callback` | HTML | Spotify redirect landing page — web only. Native apps should use their own redirect URI. |

### 4.2 Auth + users

| Method | Path | Auth | Body / Query | Returns |
|---|---|---|---|---|
| GET | `/api/users` | public | — | `[{user_id, display_name, has_pin, spotify_display_name}]` — profile picker. |
| POST | `/api/auth/signup` | public | `{display_name, pin?}` | `{user_id, display_name, session_token, has_pin}` |
| POST | `/api/auth/login` | public | `{user_id, pin?}` | `{user_id, display_name, session_token, has_pin, is_admin}` |
| POST | `/api/auth/logout` | Bearer | — | `204 No Content` |
| GET | `/api/auth/me` | Bearer | — | `{user_id, display_name, has_pin, spotify_connected, spotify_display_name, created_at, is_admin}` |
| POST | `/api/auth/spotify-link` | Bearer | `{spotify_user_id, spotify_display_name?}` | `{ok, spotify_user_id, spotify_display_name}` — associate the caller's VibeScape profile with their Spotify account. |

### 4.3 Library / catalog (Bearer required)

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/api/tracks` | `vibe_min=0..100`, `vibe_max=0..100`, `limit=20`, `mood?`, `shuffle=false` | `TrackRow[]` — the caller's ingested library filtered by vibe range + optional mood. |
| GET | `/api/tracks/search` | `q` (1–200 chars), `limit=15` (max 50) | `{tracks: TrackRow[]}` — case-insensitive substring on title/artist/album. |
| GET | `/api/tracks/random` | `vibe` (required), `tolerance=12`, `exclude_ids=comma,list` | one `TrackRow` — random pick near a vibe target. 404 if the range is empty. |
| GET | `/api/tracks/{track_key}/similar` | `limit=8` (1–25) | `{anchor:{spotify_id,apple_id,mood}, tracks: TrackRow[]}` — L1-distance similarity across ML features + mood match bonus. |
| GET | `/api/tracks/{track_key}/features` | — | Full librosa feature blob + axes for one track. |
| GET | `/api/track/{apple_id}/spotify` | — | `{spotify_id, uri}` — resolve legacy apple_id → Spotify URI. |
| POST | `/api/recompute-scores` | — | `{updated, activation_stats, mood_distribution}` — re-run scoring across the library (cheap; no audio re-analysis). |

`TrackRow` (columns are fixed and returned for every `list_tracks` /
`search_tracks` / `similar_tracks` etc. response — see
`TRACK_COLUMNS` in `backend/app.py:64`):

```
id, apple_id, title, artist, album, genre, artwork_url,
preview_url, track_view_url, duration_ms, vibe_score, mood,
spotify_id, classification_source, activation, valence,
activation_relative, acousticness, valence_mode, tempo,
energy_mean, youtube_id, energy_pred, danceability_pred,
valence_pred, vibe_score_ml, model_version,
language, language_confidence
```

### 4.4 Playback / streaming (Bearer OR ?token=)

The backend streams cached 30-second preview clips (`.m4a` / `.mp3`)
that were downloaded during ingest. HTTP range requests are fully
supported (`Accept-Ranges: bytes`), so mobile media players can seek /
resume without pulling the whole clip.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/stream/{track_key}` | `track_key` = `spotify_id` or `apple_id`. Returns 200 / 206 with `audio/mp4` or `audio/mpeg`. `Cache-Control: public, max-age=3600`. |
| GET | `/api/stream/spotify/{spotify_id}` | Same, keyed on `spotify_id` only. |

For a mobile player: pass the `?token=<session_token>` variant if your
audio component (ExoPlayer, AVPlayer, `expo-av`, `just_audio`) can't
attach custom headers.

### 4.5 YouTube fallback (Bearer required)

30-s previews aren't always available or long enough. Every track can
also be resolved to a YouTube video ID for full-length playback via the
YouTube IFrame Player.

| Method | Path | Body / Query | Returns |
|---|---|---|---|
| GET | `/api/tracks/{track_id}/youtube` | — | `{youtube_id, cached}` — never triggers a search; only returns pre-warmed IDs. |
| GET | `/api/tracks/{track_id}/youtube/search` | `q` (1–300 chars), `limit=5` (max 10) | `{results: [{youtube_id, title, channel, duration, thumbnail_url}]}` — live yt-dlp search, ~3–5 s. |
| POST | `/api/tracks/{track_id}/youtube` | `{youtube_id}` (11-char base64) | `{youtube_id, cached}` — persist a manual choice. |

`track_id` here is the numeric DB `id`, not `spotify_id`.

### 4.6 Spotify catalog + library (Bearer + `X-Spotify-Authorization`)

| Method | Path | Query / Body | Notes |
|---|---|---|---|
| GET | `/api/spotify/library` | — (auth headers only) | Returns `{liked_count, top_tracks_count, playlists: [{id, name, track_count, owner, owned_by_me}]}`. Read-only manifest for the ingest picker UI. |
| GET | `/api/spotify/search` | `q`, `limit=10` (max 10) | Wraps `/v1/search?type=track`. Each result includes `in_library` + `vibe_score` + `mood` when the caller has already ingested it, so the mobile UI can show "already added" state without a second round-trip. |

Both endpoints return `401 {"error":"spotify_token_expired"}` when the
Spotify token has aged out — the mobile app should silently refresh and
retry.

### 4.7 Ingest (Bearer, background jobs)

Ingest is asynchronous. Kick off with a POST, poll status with a GET, and
optionally cancel with DELETE. The job runs in a background thread on
the Cloud Run instance — because Cloud Run scales to zero, a client that
starts an ingest and then disconnects for a long time may lose the job
if the instance recycles.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/ingest/spotify` | `{access_token, sources:{liked, top_tracks, playlist_ids[]}}` | `202 {job_id}` — ingest the caller's own Spotify sources. `access_token` is the Spotify OAuth token. |
| POST | `/api/ingest/spotify-public` | `{playlist_url? or playlist_id?, access_token?}` | `202 {job_id, playlist_id, note}` — ingest an arbitrary public playlist. Passing the user's Spotify token dramatically improves success rate; without it Spotify's Nov 2024 lockdown 403's most playlists. |
| POST | `/api/ingest/single` | `{spotify_id, access_token?}` | `200 {status, result, track}` — synchronous single-track ingest. Idempotent. |
| POST | `/api/ingest/clear` | — | `{cleared, tracks_pruned, audio_files_removed}` — remove the caller's library. Global orphaned tracks are pruned. |
| GET | `/api/ingest/status/{job_id}` | — | Full job state (see below). Only visible to the owner. |
| DELETE | `/api/ingest/status/{job_id}` | — | `204` — request cancellation. Idempotent. |

**Job state** (poll every 1–2 s):

```json
{
  "status": "pending | running | complete | cancelled | error",
  "user_id": 42,
  "current_track": "Song - Artist",
  "total": 320,
  "processed": 107,
  "newly_analyzed": 84,
  "linked_from_global": 12,
  "already_in_library": 8,
  "no_preview": 3,
  "skipped": 0,
  "cancel_requested": false,
  "error_message": null,
  "note": "Followed playlist to enable ingest — ..."
}
```

Terminal states are `complete`, `cancelled`, and `error`. The buckets
`newly_analyzed + linked_from_global + already_in_library + no_preview +
skipped == processed`.

### 4.8 Admin (admin-only, Bearer)

Only the user matching env var `ADMIN_USER_ID` (default `1`) can call
these. Non-admins get `403 {"error":"admin_only"}`.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/admin/users` | `{users: [...]}` — all users + track counts. |
| GET | `/api/admin/users/{user_id}/stats` | Mood / source / artist breakdowns + avg vibes. |
| GET | `/api/admin/users/{user_id}/tracks` | Paginated list of a user's tracks. |
| DELETE | `/api/admin/users/{user_id}` | `204` — cascade-delete user + user_tracks + sessions. |

---

## 5. Suggested mobile flow

1. **Bootstrap.** On app start, `GET /api/health` (sanity), `GET /api/spotify/config` (Spotify client id), `GET /api/users` (profile picker).
2. **Login.** User picks a profile → `POST /api/auth/login` with pin. Store the returned `session_token` in secure storage (Keychain / EncryptedSharedPreferences).
3. **Session bootstrap.** `GET /api/auth/me` to verify + get `is_admin`. All future requests: `Authorization: Bearer <session_token>`.
4. **Player home.** `GET /api/tracks?vibe_min=…&vibe_max=…&limit=20` for the mood-slider queue. Combine with `GET /api/tracks/random?vibe=…` for shuffle-into-vibe.
5. **Playback.** For each track, try `GET /api/tracks/{track_id}/youtube` first (full song via YouTube IFrame / YouTube player SDK). Fall back to `GET /api/stream/{track_key}?token=…` for the 30-s preview. Range requests are supported so scrubbing works.
6. **Similar / autoplay.** `GET /api/tracks/{track_key}/similar?limit=8` to grow the queue continuously.
7. **Library growth.** When the user wants to add tracks:
   - **Their own Spotify library**: run OAuth Authorization Code + PKCE flow in-app, then `POST /api/ingest/spotify` with `{access_token, sources:{liked:true,...}}`. Poll `GET /api/ingest/status/{job_id}`.
   - **A shared playlist URL**: `POST /api/ingest/spotify-public` with `playlist_url` + optionally their `access_token`. Poll status.
   - **Single track from search**: `GET /api/spotify/search?q=…` with `X-Spotify-Authorization`, then `POST /api/ingest/single` with the chosen `spotify_id`.
8. **Logout.** `POST /api/auth/logout`, wipe the stored session token.

---

## 6. Notes for mobile-specific quirks

- **Background audio** — the backend just serves bytes; use OS-native
  background audio (`AVAudioSession.category = .playback` on iOS,
  `MediaSession` + `PlaybackService` on Android). The stream endpoint
  supports HTTP ranges so seek / lock-screen scrubbing works.
- **YouTube IFrame** — the web app uses the JavaScript IFrame API. On
  native, prefer the YouTube Android Player API / a WKWebView-hosted
  IFrame, or a WebView with `allowsInlineMediaPlayback = true`. The
  backend just hands you the 11-char video ID; playback is client-side.
- **Spotify OAuth redirect** — do NOT reuse the web `/callback`. Register
  a custom scheme (`vibescape://spotify-callback`) or a Universal /
  App Link with your Spotify Developer app and parse `code` in-app.
- **Cloud Run cold start** — first request after idle can take 1–3 s.
  Consider showing a splash while waiting on `/api/health` on cold boot.
- **Session-token lifetime** — no server-side expiry. Persist in secure
  storage; only prompt for PIN again on explicit logout or 401.
- **429 / rate limits** — Spotify's own limits propagate. If
  `/api/spotify/*` returns 429, back off. VibeScape itself has no
  rate-limiter beyond Cloud Run's global fair-use.
- **Streaming auth over `?token=`** — safe for LAN + HTTPS since the
  token is scrubbed from Referer and not logged in access logs. Do NOT
  extend this pattern to other endpoints.

---

## 7. Quick reference — every route

Source of truth: `backend/app.py`. All paths are relative to
`https://vibescape-241988497106.us-central1.run.app`.

```
public
  GET  /api/health
  GET  /api/moods
  GET  /api/client-config
  GET  /api/spotify/config
  GET  /api/users
  POST /api/auth/signup
  POST /api/auth/login
  GET  /callback

Bearer required
  POST   /api/auth/logout
  GET    /api/auth/me
  POST   /api/auth/spotify-link
  GET    /api/tracks
  GET    /api/tracks/search
  GET    /api/tracks/random
  GET    /api/tracks/{track_key}/similar
  GET    /api/tracks/{track_key}/features
  GET    /api/track/{apple_id}/spotify
  POST   /api/recompute-scores
  GET    /api/stream/{track_key}            (or ?token=)
  GET    /api/stream/spotify/{spotify_id}   (or ?token=)
  GET    /api/tracks/{track_id}/youtube
  GET    /api/tracks/{track_id}/youtube/search
  POST   /api/tracks/{track_id}/youtube
  POST   /api/ingest/clear
  POST   /api/ingest/spotify           (also X-Spotify-Authorization)
  POST   /api/ingest/spotify-public    (also X-Spotify-Authorization, optional)
  POST   /api/ingest/single            (also X-Spotify-Authorization, optional)
  GET    /api/ingest/status/{job_id}
  DELETE /api/ingest/status/{job_id}

Bearer + X-Spotify-Authorization
  GET    /api/spotify/library
  GET    /api/spotify/search

Admin only (Bearer + is_admin)
  GET    /api/admin/users
  GET    /api/admin/users/{user_id}/stats
  GET    /api/admin/users/{user_id}/tracks
  DELETE /api/admin/users/{user_id}
```
