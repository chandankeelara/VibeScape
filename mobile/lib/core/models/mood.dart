import 'package:freezed_annotation/freezed_annotation.dart';

part 'mood.freezed.dart';
part 'mood.g.dart';

/// One entry in the `moods[]` array returned by `GET /api/demo/moods`.
/// Powers the landing-page hero card: one real library track per vibe band.
@freezed
abstract class DemoMood with _$DemoMood {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory DemoMood({
    required String mood,
    required int vibe,
    String? title,
    String? artist,
    String? album,
    String? artworkUrl,
  }) = _DemoMood;

  factory DemoMood.fromJson(Map<String, dynamic> json) =>
      _$DemoMoodFromJson(json);
}

/// Response envelope for `GET /api/demo/moods` and `GET /api/moods`.
///
/// `moods` is a list of five `DemoMood`s for `/api/demo/moods`, or a list of
/// plain mood-name strings for `/api/moods`. We expose them as two typed
/// helpers on `VibeScapeApi` so callers pick the correct shape.
@freezed
abstract class DemoMoodsResponse with _$DemoMoodsResponse {
  const factory DemoMoodsResponse({
    required List<DemoMood> moods,
  }) = _DemoMoodsResponse;

  factory DemoMoodsResponse.fromJson(Map<String, dynamic> json) =>
      _$DemoMoodsResponseFromJson(json);
}
