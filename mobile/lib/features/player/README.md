# player

The hero screen of VibeScape. Owns the now-playing surface: album art with
mood-tinted glow, title/artist/chips, transport controls, draggable progress
scrubber, and the showpiece 0-100 vibe slider (gradient track, spring physics
on release, haptics on threshold crossings).

## Entry points

- `NowPlayingScreen` (`presentation/screens/now_playing_screen.dart`) — main
  route target for `/player`.
- `providers.dart` — barrel exporting `playerControllerProvider`,
  `vibeControllerProvider`, and the two overridable seams
  (`nativePlayerProvider`, `vibescapeApiProvider`).

## Cross-feature seams

The player consumes two abstract interfaces owned by sibling features:

| Provider | Interface | Owner |
|---|---|---|
| `nativePlayerProvider` | `NativePlayer` | Agent E — `lib/features/native_audio/` |
| `vibescapeApiProvider` | `VibescapeApi` | Agent A — `lib/features/tracks/` (or core api) |

The player feature declares both interfaces locally so it can compile and be
tested in isolation. When the sibling features land, their composition-root
overrides plug the real implementations in — no import cycles.

## State model

- `PlayerController` (`AsyncNotifier<PlayerViewState>`) subscribes to
  `NativePlayer.stateStream`. Intents: `loadAndPlay`, `play`, `pause`,
  `togglePlayPause`, `next`, `previous`, `beginScrub`, `updateScrubPosition`,
  `endScrub`, `seek`.
- `VibeController` (`Notifier<int>`) holds 0-100. `commit()` debounces a
  fetch call to `PlayerController.next(vibe:)` on gesture release so a
  rapid re-drag doesn't spam the backend.

## Animation notes

All animated widgets use `AnimationController` — never `setState` in a loop.
`VibeSlider` and `ProgressScrubber` both use `RawGestureDetector` +
`HorizontalDragGestureRecognizer` and settle via
`AnimationController.animateWith(SpringSimulation(...))` on release. The
progress scrubber decouples from the native-player position stream while
dragging (via `PlayerViewState.isScrubbing`) and reattaches on release.

## Tests

- `test/features/player/application/` — controller unit tests with
  `FakeNativePlayer` + `FakeVibescapeApi`.
- `test/features/player/presentation/` — widget tests for the slider drag,
  transport tap wiring, and golden snapshots at three vibe values.
