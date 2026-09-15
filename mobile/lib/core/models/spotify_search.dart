import 'package:freezed_annotation/freezed_annotation.dart';

part 'spotify_search.freezed.dart';
part 'spotify_search.g.dart';

/// One row in the `tracks[]` array of `GET /api/spotify/search`.
/// Same shape as an untrimmed Spotify track plus VibeScape-side annotations.
@freezed
abstract class SpotifySearchResult with _$SpotifySearchResult {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory SpotifySearchResult({
    required String spotifyId,
    required String title,
    required String artist,
    required String album,
    String? artworkUrl,
    String? previewUrl,
    int? durationMs,
    @Default(false) bool inLibrary,
    double? vibeScore,
    double? vibeScoreMl,
    String? mood,
    String? language,
    double? languageConfidence,
  }) = _SpotifySearchResult;

  factory SpotifySearchResult.fromJson(Map<String, dynamic> json) =>
      _$SpotifySearchResultFromJson(json);
}
