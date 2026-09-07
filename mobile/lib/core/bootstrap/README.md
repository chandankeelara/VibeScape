# Bootstrap layer

Wires every feature's abstract `*ApiProvider` to the concrete `VibeScapeApi`
from `lib/core/api/`. Kept here (not inside features) because features must
not import each other or reach into `core/api/`.

## Files

- `bootstrap.dart` — `productionOverrides()` returns the `List<Override>`
  applied at the root `ProviderScope` in `main.dart`.
- `api_adapters.dart` — one adapter class per feature interface:
  - `VibescapeAuthApiAdapter` implements `AuthApi`
  - `VibescapeQueueApiAdapter` implements `QueueApi`
  - `VibescapeLibraryApiAdapter` implements `LibraryApi`
  - `VibescapePlayerApiAdapter` implements `player.VibescapeApi`

## Known TODOs

The adapters were written against Agent A's `VibeScapeApi` and the feature-
local models, but a compile pass is needed to reconcile field names between
`core/models/Track` and `queue/domain/QueueTrack` / `library/domain/SearchResult`
/ `player/domain/PlayerTrack`. Everywhere a field access might be off:

- `Track.title`, `Track.artist`, `Track.artworkUrl`, `Track.durationMs`,
  `Track.spotifyUri`, `Track.vibeScore`, `Track.mood`, `Track.source` — verify
  these match Agent A's model exactly, rename where needed.
- `SpotifyLibrary.likedCount` / `.topCount` / `.playlists` — confirm.
- `SpotifySearchResult` fields accessed via `dynamic` — swap to a typed cast
  once the model shape is confirmed.
- `SyncJobStatus.added` / `.alreadyYours` / `.queued` / `.total` — confirm.
- `AuthApi.listProfiles()` currently returns `[]` because the backend
  doesn't have `GET /api/users` in the current `VibeScapeApi`. Add the
  endpoint or repurpose the picker to always show "create profile".

Run `flutter analyze` after `dart run build_runner build` to surface any
remaining mismatches.

## Testing

Tests inject their own overrides — they should NOT import
`productionOverrides()`. See `integration_test/app_smoke_test.dart` for the
override pattern.
