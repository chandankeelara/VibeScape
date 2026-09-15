import 'package:freezed_annotation/freezed_annotation.dart';

part 'spotify_auth.freezed.dart';
part 'spotify_auth.g.dart';

/// Response for `GET /api/spotify/config` — the client_id + redirect_uri
/// the frontend needs to kick off the Spotify authorization-code flow.
@freezed
abstract class SpotifyConfig with _$SpotifyConfig {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory SpotifyConfig({
    required String clientId,
    required String redirectUri,
  }) = _SpotifyConfig;

  factory SpotifyConfig.fromJson(Map<String, dynamic> json) =>
      _$SpotifyConfigFromJson(json);
}

/// Response for `GET /api/client-config` — a small runtime feature flag
/// blob used by the browser to decide whether debug UIs are visible.
@freezed
abstract class ClientConfig with _$ClientConfig {
  const factory ClientConfig({
    @Default('prod') String env,
    @Default(false) bool debug,
  }) = _ClientConfig;

  factory ClientConfig.fromJson(Map<String, dynamic> json) =>
      _$ClientConfigFromJson(json);
}

/// Response for `POST /api/spotify/refresh` — a freshly-minted access token
/// (and possibly a rotated refresh token) for the caller's Spotify session.
@freezed
abstract class SpotifyRefreshResponse with _$SpotifyRefreshResponse {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory SpotifyRefreshResponse({
    required String accessToken,
    required int expiresIn,
    String? scope,
    String? refreshToken,
  }) = _SpotifyRefreshResponse;

  factory SpotifyRefreshResponse.fromJson(Map<String, dynamic> json) =>
      _$SpotifyRefreshResponseFromJson(json);
}

/// Current state of a Spotify session as tracked by the mobile app.
/// Not a wire model — a convenience view over the pieces of an
/// `AuthResponse` that the player controller needs to keep in sync.
@freezed
abstract class SpotifyAuthState with _$SpotifyAuthState {
  const factory SpotifyAuthState({
    required String accessToken,
    required String refreshToken,
    required DateTime expiresAt,
    String? displayName,
    String? country,
    String? product,
    String? avatarUrl,
    String? scope,
  }) = _SpotifyAuthState;
}
