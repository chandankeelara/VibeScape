# library

Library search + Spotify sync. Two independent flows:

- **Search** — debounced text field with a dropdown that renders results from
  both the local library and the Spotify catalog. Debounce (250ms) lives in
  the controller so widgets stay trivial.
- **Sync modal** — bottom-sheet with two tabs: pick playlists/liked/top to
  sync, or paste a public playlist URL. Renders a live progress bar (tweened,
  never jumps) and per-bucket counts while the backend job runs.

## Entry points

- `LibrarySearchBar` — drop-in widget that owns the input + dropdown.
- `SyncModal.show(context)` — helper that pops the bottom sheet.
- `searchControllerProvider` / `syncControllerProvider` — state.

## API dependency

Controllers depend on `libraryApiProvider` (from
`application/library_api.dart`). It throws until overridden. App bootstrap
must forward it to `vibescapeApiProvider` once Agent A publishes the
matching methods (`searchTracks`, `listPlaylists`, `startSync`,
`syncJobStream`).

## Not in pubspec

None. Uses only riverpod + cached_network_image (already in deps).
