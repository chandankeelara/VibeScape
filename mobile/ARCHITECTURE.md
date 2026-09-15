# VibeScape mobile — architecture

**Read this before touching any code.** Every agent working on a feature module must follow these conventions so the codebase stays coherent.

## Stack

| Concern | Choice | Why |
|---|---|---|
| State management | **Riverpod 2 (code-gen)** | Context-free, trivially mockable in tests, compile-time safe with generators |
| Routing | **go_router 14** | Declarative, deep-link ready for `vibescape://spotify-auth` |
| HTTP | **dio 5** | Interceptors for auth + logging + retries |
| Model serialization | **freezed + json_serializable** | Immutable, generated `copyWith`, `==`, `toJson`/`fromJson` |
| Native audio | **spotify_sdk + audio_service + just_audio** | Real Spotify playback + lockscreen + local preview fallback |
| Env / secrets | **flutter_dotenv** | `.env` file at build time, `.env.example` committed |
| Testing | **flutter_test + mocktail + patrol** (integration) | Widget + unit + real-device integration |
| Lint | **very_good_analysis** | Opinionated, strict, industry baseline |

## Folder rules

- **Feature-first** under `lib/features/<name>/`. Each feature owns its own `presentation/` (widgets, screens), `application/` (Riverpod controllers/notifiers), and `domain/` (feature-specific value objects).
- **Cross-feature primitives** go in `lib/core/`. If two features would import from a third feature, promote the shared code to `lib/core/`.
- **No circular deps.** `core/` never imports from `features/`. Features never import from other features — communicate via Riverpod providers exposed by `core/`.

## Data flow

```
Widget → ref.watch(controllerProvider) → Controller → Repository → ApiClient → dio → backend
                       ↑                        ↓
                       └── AsyncValue<State> ───┘
```

- **Widgets** are dumb. They call `ref.watch(...)` and render.
- **Controllers** (`AsyncNotifier` subclasses) hold state, expose methods, catch errors → `Failure`.
- **Repositories** own data access + caching. Return `Result<T, Failure>` never raw throws.
- **ApiClient** is the only thing that talks to dio.

## Error handling

Every fallible operation returns `Result<T, Failure>` (defined in `lib/core/errors/result.dart`). No throwing across layer boundaries. `Failure` is a sealed class: `NetworkFailure`, `AuthFailure`, `NotFoundFailure`, `UnknownFailure`.

## Testing rules

1. **Every controller has a unit test.** Use `ProviderContainer` + mocked repositories.
2. **Every screen has a widget test.** Use `pumpWithProviders(...)` helper from `test/helpers/`.
3. **Vibe slider + art glow + progress scrubber have golden tests.** Snapshots live in `test/features/player/goldens/`.
4. **One integration test** boots the full app with mocked API + mocked native audio, walks profile pick → search → play → next.
5. Mocking: use `mocktail`, not `mockito` (no code-gen needed for mocks, cleaner API).

## Naming

- Files: `snake_case.dart`
- Classes: `PascalCase`
- Providers: `<thing>Provider` — e.g., `apiClientProvider`, `authControllerProvider`
- Riverpod code-gen: prefer `@riverpod` annotations over manual providers when adding new state.

## Native platform bridge

- Spotify SDK access goes through `NativePlayer` (in `lib/features/native_audio/`), NOT directly from feature controllers.
- Player controller depends on the abstract `NativePlayer` interface via a Riverpod provider; tests inject a `FakeNativePlayer`.
- Lockscreen (audio_service) is a background isolate — talks to `NativePlayer` via broadcast streams.

## Style: animations

- Use `AnimationController` + `Tween` for anything > 200ms.
- Use `SpringSimulation` (via `AnimationController.animateWith(SpringSimulation(...))`) for gesture-driven UIs (vibe slider, video-panel drag, sheet dismissal).
- Prefer `Hero` + `AnimatedSwitcher` + `AnimatedContainer` for state changes < 200ms.
- Never animate `setState` in a `build` loop — always use `AnimationController.addListener`.

## For feature agents

Your deliverable is:
1. Feature code under `lib/features/<name>/`.
2. Unit tests for every controller in `test/features/<name>/`.
3. Widget tests for every screen.
4. A one-paragraph `README.md` in your feature folder explaining what it does + entry points.
5. Providers exposed publicly go in `lib/features/<name>/providers.dart` (barrel file).

Do NOT modify:
- `lib/core/` (unless you're the core agent adding shared primitives — coordinate).
- Other features' folders.
- `pubspec.yaml` (request additions via a comment in your feature README).
