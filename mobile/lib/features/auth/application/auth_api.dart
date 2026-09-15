import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/features/auth/domain/session.dart';

/// Narrow interface the auth feature depends on. `main.dart` binds this to
/// [VibescapeAuthApiAdapter] which forwards to the shared `vibeScapeApiProvider`.
abstract class AuthApi {
  /// `POST /api/auth/login` — email + password for a native VibeScape account.
  Future<Result<Session>> emailLogin({
    required String email,
    required String password,
  });

  /// `POST /api/auth/signup` — new native account (pw ≥ 6).
  Future<Result<Session>> emailSignup({
    required String email,
    required String password,
  });

  /// `POST /api/auth/guest` — shared demo profile, no signup.
  Future<Result<Session>> guest();

  /// `POST /api/auth/spotify-oauth` — exchange an authorization code for a session.
  /// Returns the session AND the freshly-minted Spotify tokens so the caller
  /// can persist them (secure storage) for later /api/spotify/* calls.
  Future<Result<SpotifyAuthOutcome>> spotifyOauth({required String code});

  /// `GET /api/spotify/config` → `{client_id, redirect_uri}`. Used to construct
  /// the authorize URL.
  Future<Result<SpotifyOAuthConfig>> spotifyConfig();

  /// `GET /api/auth/me` — hydrate a persisted bearer token on cold start.
  Future<Result<Session>> hydrate(String bearerToken);

  /// `POST /api/auth/logout`.
  Future<Result<void>> logout();
}

class SpotifyAuthOutcome {
  const SpotifyAuthOutcome({
    required this.session,
    this.accessToken,
    this.refreshToken,
    this.expiresIn,
  });
  final Session session;
  final String? accessToken;
  final String? refreshToken;
  final int? expiresIn;
}

class SpotifyOAuthConfig {
  const SpotifyOAuthConfig({required this.clientId, required this.redirectUri});
  final String clientId;
  final String redirectUri;
}

final authApiProvider = Provider<AuthApi>((ref) {
  throw UnimplementedError(
    'authApiProvider must be overridden at ProviderScope. Bind it to '
    'VibescapeAuthApiAdapter in lib/core/bootstrap/bootstrap.dart.',
  );
});
