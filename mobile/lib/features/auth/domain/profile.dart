import 'package:freezed_annotation/freezed_annotation.dart';

part 'profile.freezed.dart';
part 'profile.g.dart';

/// A VibeScape user profile as returned by `GET /api/users` and the
/// `/api/auth/*` endpoints.
@freezed
abstract class Profile with _$Profile {
  const factory Profile({
    required String userId,
    required String displayName,
    @Default(false) bool hasPin,
    @Default(false) bool spotifyConnected,
    @Default(false) bool isAdmin,
    String? avatarUrl,
  }) = _Profile;

  factory Profile.fromJson(Map<String, dynamic> json) =>
      _$ProfileFromJson(json);
}

/// Returns up to two initial letters used as an avatar fallback, mirroring
/// `initialsFor()` in `frontend/app.js`.
String initialsFor(String? name) {
  final trimmed = (name ?? '').trim();
  if (trimmed.isEmpty) return '?';
  final parts = trimmed.split(RegExp(r'\s+')).where((p) => p.isNotEmpty).toList();
  if (parts.isEmpty) return '?';
  if (parts.length == 1) {
    return parts.first.substring(0, 1).toUpperCase();
  }
  return (parts.first.substring(0, 1) + parts.last.substring(0, 1)).toUpperCase();
}
