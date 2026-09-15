import 'package:freezed_annotation/freezed_annotation.dart';

part 'youtube.freezed.dart';
part 'youtube.g.dart';

/// Response for `GET /api/tracks/{track_id}/youtube`. `youtubeId` is null
/// when no cached video exists for the track — the endpoint never triggers
/// a live search.
@freezed
abstract class YouTubeLookup with _$YouTubeLookup {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory YouTubeLookup({
    String? youtubeId,
    @Default(true) bool cached,
  }) = _YouTubeLookup;

  factory YouTubeLookup.fromJson(Map<String, dynamic> json) =>
      _$YouTubeLookupFromJson(json);
}

/// One row in the `results[]` array of
/// `GET /api/tracks/{track_id}/youtube/search`.
@freezed
abstract class YouTubeSearchResult with _$YouTubeSearchResult {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory YouTubeSearchResult({
    required String youtubeId,
    required String title,
    String? channel,
    int? duration,
    String? thumbnailUrl,
  }) = _YouTubeSearchResult;

  factory YouTubeSearchResult.fromJson(Map<String, dynamic> json) =>
      _$YouTubeSearchResultFromJson(json);
}
