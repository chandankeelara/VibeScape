import 'package:freezed_annotation/freezed_annotation.dart';

part 'track_features.freezed.dart';
part 'track_features.g.dart';

/// Nested `features` blob returned inside `GET /api/tracks/{key}/features`.
/// Every field is nullable — the DB row may lack analysis output.
@freezed
abstract class TrackFeatureVector with _$TrackFeatureVector {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory TrackFeatureVector({
    double? tempo,
    double? tempoStability,
    double? onsetRate,
    double? energyMean,
    double? energyStd,
    double? brightness,
    double? bandwidth,
    double? rolloff,
    double? spectralContrast,
    double? flatness,
    double? zcr,
    double? timbreVariability,
    double? valenceMode,
    double? tonnetzStd,
    double? acousticness,
    @Default([]) List<double> mfccMean,
    @Default([]) List<double> chromaMean,
  }) = _TrackFeatureVector;

  factory TrackFeatureVector.fromJson(Map<String, dynamic> json) =>
      _$TrackFeatureVectorFromJson(json);
}

/// Nested `axes` blob returned inside `GET /api/tracks/{key}/features`.
@freezed
abstract class TrackAxes with _$TrackAxes {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory TrackAxes({
    double? activation,
    double? valence,
    double? activationRelative,
  }) = _TrackAxes;

  factory TrackAxes.fromJson(Map<String, dynamic> json) =>
      _$TrackAxesFromJson(json);
}

/// Response for `GET /api/tracks/{key}/features` — the full stored feature
/// blob plus derived axes for a track.
@freezed
abstract class TrackFeaturesResponse with _$TrackFeaturesResponse {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory TrackFeaturesResponse({
    int? appleId,
    String? spotifyId,
    String? title,
    String? artist,
    required TrackFeatureVector features,
    required TrackAxes axes,
    String? mood,
    String? classificationSource,
  }) = _TrackFeaturesResponse;

  factory TrackFeaturesResponse.fromJson(Map<String, dynamic> json) =>
      _$TrackFeaturesResponseFromJson(json);
}
