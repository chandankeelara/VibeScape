import 'package:freezed_annotation/freezed_annotation.dart';

part 'recompute.freezed.dart';
part 'recompute.g.dart';

/// Response for `POST /api/recompute-scores` — a summary of what changed
/// when the caller's tracks were re-scored.
///
/// The backend returns arbitrary counters; kept loose as a Map plus a few
/// commonly-inspected fields so callers can drill in without a schema break.
@freezed
abstract class RecomputeSummary with _$RecomputeSummary {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory RecomputeSummary({
    int? tracksTouched,
    int? tracksSkipped,
    Map<String, int>? moodDistribution,
  }) = _RecomputeSummary;

  factory RecomputeSummary.fromJson(Map<String, dynamic> json) =>
      _$RecomputeSummaryFromJson(json);
}
