import 'package:freezed_annotation/freezed_annotation.dart';
import 'package:vibescape/core/models/track.dart';

part 'ingest_single.freezed.dart';
part 'ingest_single.g.dart';

/// Response for `POST /api/ingest/single` — a wrapped track row with a
/// status tag ("ok" | "already_ingested") plus the raw pipeline `result`
/// string when a fresh ingest ran.
@freezed
abstract class SingleIngestResponse with _$SingleIngestResponse {
  const factory SingleIngestResponse({
    required String status,
    required Track track,
    String? result,
  }) = _SingleIngestResponse;

  factory SingleIngestResponse.fromJson(Map<String, dynamic> json) =>
      _$SingleIngestResponseFromJson(json);
}
