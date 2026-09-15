# VibeScape Mobile (Flutter)

Cross-platform (iOS + Android + macOS + Windows + web) rewrite of the VibeScape frontend in Flutter. Talks to the same FastAPI backend as the web app — no backend changes.

## Why Flutter (vs. Capacitor / RN / native)

- **Best-in-class animations** (Impeller renderer, direct-to-GPU). The vibe slider, art glow, progress scrubbing, video-panel drag all get 60/120fps with physics-based motion.
- **Single codebase → iOS + Android** (and desktop later). No parallel Swift+Kotlin rewrites.
- **Cloud iOS builds** via Codemagic — no Mac required for CI.
- **First-party Spotify iOS SDK bindings** via the `spotify_sdk` package (wraps SPTAppRemote + SPTSessionManager).

## Prereqs (local dev)

- Flutter SDK 3.24+ (`flutter --version`)
- Dart 3.5+
- Android Studio (Android) OR Xcode + macOS (iOS, or use Codemagic for cloud builds)

## First-run setup

```bash
cd mobile
flutter create --org com.vibescape --project-name vibescape --platforms=ios,android .   # generates native shells
flutter pub get
dart run build_runner build --delete-conflicting-outputs                                  # generates freezed/json code
flutter run
```

Point the app at your backend by copying `.env.example` → `.env` and setting `VIBESCAPE_API_BASE_URL`.

## Layout

```
mobile/
├── ARCHITECTURE.md        — read this before touching code
├── pubspec.yaml           — deps + assets
├── analysis_options.yaml  — strict lints
├── .env.example
├── lib/
│   ├── main.dart          — entrypoint, ProviderScope wrap
│   ├── app.dart           — MaterialApp.router
│   ├── core/
│   │   ├── env/           — runtime config
│   │   ├── theme/         — colors, typography, spacing tokens
│   │   ├── router/        — go_router config + auth guard
│   │   ├── api/           — dio client, interceptors, endpoint definitions
│   │   ├── models/        — freezed data classes for API responses
│   │   ├── errors/        — Failure + Result<T> types
│   │   └── widgets/       — shared primitives (ArtImage, Chip, Sheet, …)
│   └── features/
│       ├── auth/          — profile picker + Spotify OAuth
│       ├── player/        — now-playing screen, transport, vibe slider
│       ├── queue/         — queue + DJ recommendations bottom sheet
│       ├── library/       — search, Spotify sync, playlist URL
│       └── native_audio/  — Spotify SDK bridge + audio_service integration
├── test/
│   ├── core/
│   ├── features/
│   └── helpers/           — pump helpers, mock providers, golden test setup
└── integration_test/
```

## Non-goals

- Do NOT rewrite the backend or ingest pipeline.
- Do NOT bypass Spotify Premium requirement.
- Do NOT publish to App Store yet — TestFlight/Play Internal only.
