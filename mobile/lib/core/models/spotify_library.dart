import 'package:freezed_annotation/freezed_annotation.dart';

part 'spotify_library.freezed.dart';
part 'spotify_library.g.dart';

/// One row in the `playlists[]` array of `GET /api/spotify/library`.
@freezed
abstract class PlaylistSummary with _$PlaylistSummary {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory PlaylistSummary({
    required String id,
    required String name,
    @Default(0) int trackCount,
    String? owner,
    @Default(false) bool ownedByMe,
  }) = _PlaylistSummary;

  factory PlaylistSummary.fromJson(Map<String, dynamic> json) =>
      _$PlaylistSummaryFromJson(json);
}

/// Response for `GET /api/spotify/library` — the manifest the sync modal
/// renders before the user picks sources to ingest.
@freezed
abstract class SpotifyLibrary with _$SpotifyLibrary {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory SpotifyLibrary({
    @Default(0) int likedCount,
    @Default(0) int topTracksCount,
    @Default([]) List<PlaylistSummary> playlists,
  }) = _SpotifyLibrary;

  factory SpotifyLibrary.fromJson(Map<String, dynamic> json) =>
      _$SpotifyLibraryFromJson(json);
}
