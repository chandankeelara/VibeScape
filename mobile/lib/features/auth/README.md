# auth

Owns VibeScape sign-in, sign-up, and Spotify OAuth linking for the mobile
client. The entry screen is `ProfilePickerScreen` ("Who's listening?"), which
routes into `PinEntryScreen` for PIN-protected profiles or
`CreateProfileScreen` for new users; state is centralised in
`authControllerProvider` (an `AsyncNotifier<AuthState>` in
`application/auth_controller.dart`). Spotify linking is handled separately by
`spotifyAuthControllerProvider`, which launches the authorize URL via
`url_launcher` and listens for the `vibescape://spotify-auth` deep-link
callback via `app_links`. Device-local persistence (last-used profile in
`SharedPreferences`, session token and Spotify tokens in
`FlutterSecureStorage`) is wrapped by `AuthLocalStorage`.

## Public surface

Consumers should only import `package:vibescape/features/auth/providers.dart`.

## Backend contract

Endpoints mirror `frontend/app.js`: `GET /api/users`, `POST /api/auth/login`,
`POST /api/auth/signup`, `GET /api/auth/me`, `POST /api/auth/logout`, and the
Spotify link/callback pair. The feature depends on the abstract `AuthApi`
(`application/auth_api.dart`); once Agent A publishes
`lib/core/api/vibescape_api.dart`, wire `authApiProvider` to a thin adapter
around the shared `vibescapeApiProvider` and delete `AuthApi`.

## Deps requested

None beyond `pubspec.yaml`.
