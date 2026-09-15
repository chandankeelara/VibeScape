import 'package:freezed_annotation/freezed_annotation.dart';

part 'track.freezed.dart';
part 'track.g.dart';

/// A track row as returned by `GET /api/tracks`, `GET /api/tracks/random`,
/// `GET /api/tracks/search`, and the `tracks[]` array in
/// `GET|POST /api/tracks/{key}/similar` and `POST /api/ingest/single`.
///
/// Mirrors the `TRACK_COLUMNS` list in `backend/app.py` plus the optional
/// `score` field that recommender responses layer on top.
@freezed
abstract class Track with _$Track {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory Track({
    int? id,
    int? appleId,
    String? title,
    String? artist,
    String? album,
    String? genre,
    String? artworkUrl,
    String? previewUrl,
    String? trackViewUrl,
    int? durationMs,
    double? vibeScore,
    String? mood,
    String? spotifyId,
    String? classificationSource,
    double? activation,
    double? valence,
    double? activationRelative,
    double? acousticness,
    double? valenceMode,
    double? tempo,
    double? energyMean,
    String? youtubeId,
    double? energyPred,
    double? danceabilityPred,
    double? valencePred,
    double? vibeScoreMl,
    String? modelVersion,
    String? language,
    double? languageConfidence,
    String? ingestionStatus,
    double? score,
  }) = _Track;

  factory Track.fromJson(Map<String, dynamic> json) => _$TrackFromJson(json);
}
