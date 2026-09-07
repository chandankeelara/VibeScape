import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/features/auth/application/auth_api.dart';
import 'package:vibescape/features/auth/data/auth_local_storage.dart';
import 'package:vibescape/features/auth/domain/auth_state.dart';
import 'package:vibescape/features/auth/domain/session.dart';

/// Single AsyncNotifier driving the auth flow. Screens call intents
/// (`signInGuest`, `signInEmail`, `submitSpotifyCode`, `signOut`) and watch
/// the `AsyncValue<AuthState>` for what to render.
class AuthController extends AsyncNotifier<AuthState> {
  @override
  Future<AuthState> build() async {
    final storage = await ref.read(authLocalStorageProvider.future);
    final token = await storage.getSessionToken();
    if (token == null) return const AuthState.signedOut();

    final api = ref.read(authApiProvider);
    final res = await api.hydrate(token);
    switch (res) {
      case Ok<Session>(:final value):
        return AuthState.authenticated(session: value);
      case Err<Session>():
        await storage.clearSessionToken();
        return const AuthState.signedOut();
    }
  }

  Future<void> signInGuest() =>
      _run((api) => api.guest(), hint: 'Signing in as guest…');

  Future<void> signInEmail({
    required String email,
    required String password,
  }) =>
      _run(
        (api) => api.emailLogin(email: email, password: password),
        hint: 'Signing in…',
      );

  Future<void> signUpEmail({
    required String email,
    required String password,
  }) =>
      _run(
        (api) => api.emailSignup(email: email, password: password),
        hint: 'Creating account…',
      );

  /// Called by SpotifyAuthController after it captures the `?code=...`
  /// callback (deep link on mobile, current URL on web).
  Future<void> submitSpotifyCode(String code) async {
    state = const AsyncData(AuthState.busy(hint: 'Linking Spotify…'));
    final api = ref.read(authApiProvider);
    final storage = await ref.read(authLocalStorageProvider.future);
    final res = await api.spotifyOauth(code: code);
    switch (res) {
      case Ok<SpotifyAuthOutcome>(:final value):
        await storage.setSessionToken(value.session.token);
        if (value.accessToken != null) {
          await storage.setSpotifyToken(
            value.session.profile.userId,
            value.accessToken!,
          );
        }
        state = AsyncData(AuthState.authenticated(session: value.session));
      case Err<SpotifyAuthOutcome>(:final failure):
        state = AsyncData(AuthState.failed(failure: failure));
    }
  }

  Future<void> signOut() async {
    final api = ref.read(authApiProvider);
    final storage = await ref.read(authLocalStorageProvider.future);
    await api.logout();
    await storage.clearSessionToken();
    state = const AsyncData(AuthState.signedOut());
  }

  Future<void> _run(
    Future<Result<Session>> Function(AuthApi api) fn, {
    required String hint,
  }) async {
    state = AsyncData(AuthState.busy(hint: hint));
    final api = ref.read(authApiProvider);
    final storage = await ref.read(authLocalStorageProvider.future);
    final res = await fn(api);
    switch (res) {
      case Ok<Session>(:final value):
        await storage.setSessionToken(value.token);
        state = AsyncData(AuthState.authenticated(session: value));
      case Err<Session>(:final failure):
        state = AsyncData(AuthState.failed(failure: failure));
    }
  }
}

final authControllerProvider =
    AsyncNotifierProvider<AuthController, AuthState>(AuthController.new);
