import 'package:freezed_annotation/freezed_annotation.dart';

part 'ingest_clear.freezed.dart';
part 'ingest_clear.g.dart';

/// Response for `POST /api/ingest/clear` — how many rows were unlinked from
/// the caller's library and how many global tracks / audio files were
/// pruned in the follow-up sweep. The backend returns arbitrary keys; we
/// capture the ones the frontend consumes.
@freezed
abstract class IngestClearResult with _$IngestClearResult {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory IngestClearResult({
    @Default(0) int cleared,
    @Default(0) int removedTracks,
    @Default(0) int removedFiles,
  }) = _IngestClearResult;

  factory IngestClearResult.fromJson(Map<String, dynamic> json) =>
      _$IngestClearResultFromJson(json);
}
