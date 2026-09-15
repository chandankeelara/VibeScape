import 'package:freezed_annotation/freezed_annotation.dart';

part 'session_user.freezed.dart';
part 'session_user.g.dart';

/// Response for `POST /api/auth/signup`, `POST /api/auth/login`,
/// `POST /api/auth/guest`, and `POST /api/auth/spotify-oauth`.
///
/// `spotifyAccessToken` / `spotifyRefreshToken` / `spotifyExpiresIn` /
/// `spotifyScope` are only present on the spotify-oauth response.
@freezed
abstract class AuthResponse with _$AuthResponse {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory AuthResponse({
    required int userId,
    required String displayName,
    required String sessionToken,
    @Default(false) bool isAdmin,
    @Default(false) bool isGuest,
    @Default(false) bool spotifyConnected,
    @Default(false) bool isPremium,
    String? email,
    String? spotifyDisplayName,
    String? spotifyEmail,
    String? spotifyCountry,
    String? spotifyProduct,
    String? avatarUrl,
    String? profileUrl,
    String? spotifyAccessToken,
    String? spotifyRefreshToken,
    int? spotifyExpiresIn,
    String? spotifyScope,
  }) = _AuthResponse;

  factory AuthResponse.fromJson(Map<String, dynamic> json) =>
      _$AuthResponseFromJson(json);
}

/// Response for `GET /api/auth/me`. Superset of the login payload with
/// created/last-login timestamps and profile URLs.
@freezed
abstract class MeResponse with _$MeResponse {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory MeResponse({
    required int userId,
    required String displayName,
    @Default(false) bool spotifyConnected,
    @Default(false) bool isPremium,
    @Default(false) bool isAdmin,
    @Default(false) bool isGuest,
    String? spotifyDisplayName,
    String? spotifyEmail,
    String? spotifyCountry,
    String? spotifyProduct,
    String? avatarUrl,
    String? profileUrl,
    String? createdAt,
    String? lastLoginAt,
  }) = _MeResponse;

  factory MeResponse.fromJson(Map<String, dynamic> json) =>
      _$MeResponseFromJson(json);
}
