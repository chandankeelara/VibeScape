import 'package:freezed_annotation/freezed_annotation.dart';
import 'package:vibescape/core/models/track.dart';

part 'similar_tracks.freezed.dart';
part 'similar_tracks.g.dart';

/// Anchor descriptor returned inside every `/api/tracks/{key}/similar`
/// response.
@freezed
abstract class SimilarAnchor with _$SimilarAnchor {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory SimilarAnchor({
    String? spotifyId,
    int? appleId,
    String? mood,
  }) = _SimilarAnchor;

  factory SimilarAnchor.fromJson(Map<String, dynamic> json) =>
      _$SimilarAnchorFromJson(json);
}

/// Response envelope for `GET /api/tracks/{key}/similar` and its POST twin.
///
/// `modeUsed` echoes what the backend actually ran ("vibe" or "dj").
/// `variantUsed` is set when DJ mode ran ("fused" | "mert").
@freezed
abstract class SimilarTracksResponse with _$SimilarTracksResponse {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory SimilarTracksResponse({
    required SimilarAnchor anchor,
    required List<Track> tracks,
    String? modeUsed,
    String? variantUsed,
  }) = _SimilarTracksResponse;

  factory SimilarTracksResponse.fromJson(Map<String, dynamic> json) =>
      _$SimilarTracksResponseFromJson(json);
}

/// `{id, weight}` entry accepted in `positive_ids` / `negative_ids` on the
/// DJ-mode POST body of `/api/tracks/{key}/similar`.
@freezed
abstract class WeightedTrackId with _$WeightedTrackId {
  const factory WeightedTrackId({
    required String id,
    @Default(1.0) double weight,
  }) = _WeightedTrackId;

  factory WeightedTrackId.fromJson(Map<String, dynamic> json) =>
      _$WeightedTrackIdFromJson(json);
}
