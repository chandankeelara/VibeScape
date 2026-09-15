/// Public barrel for the auth feature.
///
/// Other features and `main.dart` should only import from this file — never
/// reach into `application/`, `data/`, or `domain/` directly.
library;

export 'application/auth_api.dart'
    show AuthApi, SpotifyAuthOutcome, SpotifyOAuthConfig, authApiProvider;
export 'application/auth_controller.dart'
    show AuthController, authControllerProvider;
export 'application/spotify_auth_controller.dart'
    show SpotifyAuthController, spotifyAuthControllerProvider;
export 'data/auth_local_storage.dart'
    show AuthLocalStorage, authLocalStorageProvider;
export 'domain/auth_state.dart';
export 'domain/profile.dart' show Profile;
export 'domain/session.dart' show Session;
export 'presentation/screens/landing_screen.dart' show LandingScreen;
export 'presentation/widgets/auth_modal.dart' show AuthModal;
