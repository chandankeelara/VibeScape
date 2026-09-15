import 'package:freezed_annotation/freezed_annotation.dart';

part 'admin.freezed.dart';
part 'admin.g.dart';

/// One user row returned by `GET /api/admin/users`.
@freezed
abstract class AdminUser with _$AdminUser {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory AdminUser({
    required int userId,
    required String displayName,
    @Default(0) int trackCount,
    @Default(false) bool isAdmin,
    @Default(false) bool isGuest,
    String? spotifyUserId,
    String? spotifyDisplayName,
    String? spotifyEmail,
    String? spotifyCountry,
    String? spotifyProduct,
    String? avatarUrl,
    String? createdAt,
    String? lastLoginAt,
  }) = _AdminUser;

  factory AdminUser.fromJson(Map<String, dynamic> json) =>
      _$AdminUserFromJson(json);
}

/// Response for `GET /api/admin/users/{user_id}/stats`.
@freezed
abstract class AdminUserStats with _$AdminUserStats {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory AdminUserStats({
    required int userId,
    required String displayName,
    @Default(0) int trackCount,
    String? spotifyDisplayName,
    String? createdAt,
    @Default([]) List<AdminMoodCount> byMood,
    @Default([]) List<AdminSourceCount> bySource,
    @Default([]) List<AdminArtistCount> topArtists,
    double? avgVibeMl,
    double? avgActivation,
    double? avgValence,
  }) = _AdminUserStats;

  factory AdminUserStats.fromJson(Map<String, dynamic> json) =>
      _$AdminUserStatsFromJson(json);
}

@freezed
abstract class AdminMoodCount with _$AdminMoodCount {
  const factory AdminMoodCount({
    required String mood,
    required int count,
  }) = _AdminMoodCount;

  factory AdminMoodCount.fromJson(Map<String, dynamic> json) =>
      _$AdminMoodCountFromJson(json);
}

@freezed
abstract class AdminSourceCount with _$AdminSourceCount {
  const factory AdminSourceCount({
    required String source,
    required int count,
  }) = _AdminSourceCount;

  factory AdminSourceCount.fromJson(Map<String, dynamic> json) =>
      _$AdminSourceCountFromJson(json);
}

@freezed
abstract class AdminArtistCount with _$AdminArtistCount {
  const factory AdminArtistCount({
    required String artist,
    required int count,
  }) = _AdminArtistCount;

  factory AdminArtistCount.fromJson(Map<String, dynamic> json) =>
      _$AdminArtistCountFromJson(json);
}

/// One row in the `tracks[]` array of `GET /api/admin/users/{user_id}/tracks`.
/// A slimmer projection than the shared `Track` model — only the columns the
/// admin dashboard needs.
@freezed
abstract class AdminUserTrack with _$AdminUserTrack {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory AdminUserTrack({
    required int id,
    String? title,
    String? artist,
    String? album,
    String? mood,
    double? vibeScoreMl,
    String? classificationSource,
    String? addedAt,
    @Default(0) int playCount,
  }) = _AdminUserTrack;

  factory AdminUserTrack.fromJson(Map<String, dynamic> json) =>
      _$AdminUserTrackFromJson(json);
}
