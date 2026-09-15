# native_audio

Owns the platform bridge for full-track Spotify playback (via `spotify_sdk`),
lockscreen / OS media transport (via `audio_service`), and the local preview
fallback (via `just_audio`).

This is the only feature allowed to talk to the Spotify SDK — every other
feature (player controller, DJ engine, ...) depends on the abstract
`NativePlayer` interface via `nativePlayerProvider`.

## Files

| Path | Role |
|---|---|
| `domain/native_player.dart` | Abstract `NativePlayer` interface (Result-returning). |
| `domain/player_state.dart` | Immutable `PlayerState` freezed model. |
| `domain/remote_command.dart` | Sealed `RemoteCommand` union: Play/Pause/Next/Previous. |
| `data/spotify_native_player.dart` | Real impl on top of `SpotifySdk` + `subscribePlayerState()`. |
| `data/fake_native_player.dart` | In-memory fake with public `StreamController`s for tests. |
| `data/audio_service_handler.dart` | `BaseAudioHandler` subclass that bridges OS transport events into a `RemoteCommand` stream. |
| `providers.dart` | Riverpod providers + barrel exports. |

## Swap real <-> fake

Override at `ProviderScope`:

```dart
// Tests
ProviderScope(
  overrides: [
    nativePlayerProvider.overrideWithValue(FakeNativePlayer(authenticated: true)),
  ],
  child: MyWidget(),
)

// Production (wire-up phase / task #7 — see main.dart TODO)
final handler = await AudioService.init(
  builder: VibescapeAudioHandler.new,
  config: const AudioServiceConfig(
    androidNotificationChannelId: 'app.vibescape.audio',
    androidNotificationChannelName: 'VibeScape playback',
    androidNotificationOngoing: true,
  ),
);
final player = SpotifyNativePlayer(
  clientId: dotenv.env['SPOTIFY_CLIENT_ID']!,
  redirectUrl: dotenv.env['SPOTIFY_REDIRECT_URL']!,
  audioHandler: handler,
);
runApp(
  ProviderScope(
    overrides: [nativePlayerProvider.overrideWithValue(player)],
    child: const VibescapeApp(),
  ),
);
```

**Left for task #7 (wire-up):** register the override in `lib/main.dart`. This
feature intentionally does NOT edit `main.dart`.

## Failure mapping (SpotifyNativePlayer)

| Underlying condition | `Failure` variant |
|---|---|
| Spotify app not installed | `Failure.notFound` |
| Non-premium account (`UserNotAuthorized`) | `Failure.auth` |
| Token expired / never signed in (`NotLoggedIn`, `AuthenticationFailed`) | `Failure.auth` |
| App-remote link dropped (`Disconnected`, `CouldNotConnect`) | `Failure.network` |
| Anything else | `Failure.unknown` |

The web equivalent lives in `frontend/app.js` around the
`player_state_changed` / `authentication_error` / `account_error` listeners —
consult it for expected recovery behaviour (auth-expired -> re-login,
account-error -> toast + downgrade to preview fallback).

## Platform config required (for wire-up phase)

`flutter create` will regenerate the native shells; the following keys must
then be added before `spotify_sdk` + `audio_service` work end-to-end:

### iOS — `ios/Runner/Info.plist`
* `LSApplicationQueriesSchemes` -> `spotify` (so `SPTAppRemote` can detect the
  Spotify app)
* `CFBundleURLTypes` -> add a URL scheme matching the Spotify redirect URI
  (e.g. `vibescape`) for OAuth callback
* `UIBackgroundModes` -> `audio` (required for `audio_service` background
  playback + lockscreen controls)
* `NSAppleMusicUsageDescription` -> short user-facing string (some Spotify SDK
  builds surface this)

### Android — `android/app/src/main/AndroidManifest.xml`
* `<queries>` block listing `com.spotify.music` (Android 11+ package
  visibility requirement)
* `<uses-permission android:name="android.permission.INTERNET"/>`
* `<uses-permission android:name="android.permission.FOREGROUND_SERVICE"/>`
* `<uses-permission android:name="android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK"/>`
  (Android 14+)
* `<service android:name="com.ryanheise.audioservice.AudioService" ...>` with
  the `MediaBrowserService` intent-filter (see `audio_service` README)
* `<receiver android:name="com.ryanheise.audioservice.MediaButtonReceiver" ...>`
  for bluetooth-headset media keys
* Deep-link intent-filter on `MainActivity` matching the Spotify redirect URI

### `.env` keys
* `SPOTIFY_CLIENT_ID`
* `SPOTIFY_REDIRECT_URL` (must match the URL scheme registered above)
