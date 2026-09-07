# Core API layer

The single typed entry point to the VibeScape FastAPI backend lives at
`vibescape_api.dart`. Every method returns `Result<T, Failure>` — errors
never escape as exceptions. Wire error mapping (401 → auth, 404 →
notFound, 5xx → network, etc.) lives in `dio_client.dart` via
`failureFromDioError`.

## Endpoint map

Every method here corresponds 1:1 to a route in `backend/app.py`.

### Health + config

| Method                    | Route                                |
|---------------------------|--------------------------------------|
| `health()`                | `GET  /api/health`                   |
| `spotifyConfig()`         | `GET  /api/spotify/config`           |
| `clientConfig()`          | `GET  /api/client-config`            |
| `moods()`                 | `GET  /api/moods`                    |
| `demoMoods()`             | `GET  /api/demo/moods`               |

### Auth

| Method                    | Route                                |
|---------------------------|--------------------------------------|
| `signup()`                | `POST /api/auth/signup`              |
| `login()`                 | `POST /api/auth/login`               |
| `guest()`                 | `POST /api/auth/guest`               |
| `spotifyOauth()`          | `POST /api/auth/spotify-oauth`       |
| `spotifyRefresh()`        | `POST /api/spotify/refresh`          |
| `logout()`                | `POST /api/auth/logout`              |
| `me()`                    | `GET  /api/auth/me`                  |
| `spotifyLink()`           | `POST /api/auth/spotify-link`        |

### Tracks / recommendations

| Method                    | Route                                          |
|---------------------------|------------------------------------------------|
| `listTracks()`            | `GET  /api/tracks`                             |
| `searchTracks()`          | `GET  /api/tracks/search`                      |
| `randomTrack()`           | `GET  /api/tracks/random`                      |
| `similarTracks()`         | `GET  /api/tracks/{track_key}/similar`         |
| `similarTracksPost()`     | `POST /api/tracks/{track_key}/similar`         |
| `trackFeatures()`         | `GET  /api/tracks/{track_key}/features`        |
| `trackSpotifyLink()`      | `GET  /api/track/{apple_id}/spotify`           |
| `recomputeScores()`       | `POST /api/recompute-scores`                   |

### YouTube

| Method                    | Route                                          |
|---------------------------|------------------------------------------------|
| `youtubeLookup()`         | `GET  /api/tracks/{track_id}/youtube`          |
| `youtubeSearch()`         | `GET  /api/tracks/{track_id}/youtube/search`   |
| `setYoutubeId()`          | `POST /api/tracks/{track_id}/youtube`          |

### Spotify (server-mediated)

| Method                    | Route                                |
|---------------------------|--------------------------------------|
| `spotifyLibrary()`        | `GET  /api/spotify/library`          |
| `spotifySearch()`         | `GET  /api/spotify/search`           |

Both accept the caller's raw Spotify OAuth token (forwarded in the
`X-Spotify-Authorization` header the backend expects).

### Ingest

| Method                    | Route                                       |
|---------------------------|---------------------------------------------|
| `ingestClear()`           | `POST   /api/ingest/clear`                  |
| `ingestSingle()`          | `POST   /api/ingest/single`                 |
| `ingestSpotify()`         | `POST   /api/ingest/spotify`                |
| `ingestSpotifyPublic()`   | `POST   /api/ingest/spotify-public`         |
| `ingestStatus()`          | `GET    /api/ingest/status/{job_id}`        |
| `cancelIngest()`          | `DELETE /api/ingest/status/{job_id}`        |
| `pollIngestStatus()`      | Stream wrapper — polls `ingestStatus` every 500ms until a terminal state (`complete` / `error` / `cancelled`). |

### Admin

| Method                    | Route                                        |
|---------------------------|----------------------------------------------|
| `adminUsers()`            | `GET    /api/admin/users`                    |
| `adminUserStats()`        | `GET    /api/admin/users/{user_id}/stats`    |
| `adminUserTracks()`       | `GET    /api/admin/users/{user_id}/tracks`   |
| `adminDeleteUser()`       | `DELETE /api/admin/users/{user_id}`          |

### Streaming URLs

| Method                    | Route                                        |
|---------------------------|----------------------------------------------|
| `streamUrlByKey()`        | `GET /api/stream/{track_key}`                |
| `spotifyStreamUrl()`      | `GET /api/stream/spotify/{spotify_id}`       |

These return `Uri`s (with `?token=` appended) instead of making a
request, because `just_audio` / `audio_service` fetch the URL directly
and cannot attach auth headers to their src.

## Models

Response DTOs live under `../models/`, grouped by domain:

- `track.dart`               — `Track`
- `mood.dart`                — `DemoMood`, `DemoMoodsResponse`
- `session_user.dart`        — `AuthResponse`, `MeResponse`
- `similar_tracks.dart`      — `SimilarTracksResponse`, `SimilarAnchor`, `WeightedTrackId`
- `spotify_library.dart`     — `SpotifyLibrary`, `PlaylistSummary`
- `spotify_search.dart`      — `SpotifySearchResult`
- `spotify_auth.dart`        — `SpotifyConfig`, `ClientConfig`, `SpotifyRefreshResponse`, `SpotifyAuthState`
- `sync_job.dart`            — `SyncJobStart`, `SyncJobStatus`, `IngestSourcesBody`
- `youtube.dart`             — `YouTubeLookup`, `YouTubeSearchResult`
- `track_features.dart`      — `TrackFeaturesResponse`, `TrackFeatureVector`, `TrackAxes`
- `admin.dart`               — `AdminUser`, `AdminUserStats`, `AdminUserTrack`, `AdminMoodCount`, `AdminSourceCount`, `AdminArtistCount`
- `recompute.dart`           — `RecomputeSummary`
- `ingest_single.dart`       — `SingleIngestResponse`
- `ingest_clear.dart`        — `IngestClearResult`
- `track_spotify.dart`       — `TrackSpotifyLink`
- `health.dart`              — `HealthStatus`
