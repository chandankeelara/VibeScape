import 'package:freezed_annotation/freezed_annotation.dart';

part 'sync_job.freezed.dart';
part 'sync_job.g.dart';

/// Response for `POST /api/ingest/spotify` and `POST /api/ingest/spotify-public`
/// — a lightweight envelope with just the id needed to start polling status.
///
/// The `playlist_id` and `note` fields are only present on the public-playlist
/// variant.
@freezed
abstract class SyncJobStart with _$SyncJobStart {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory SyncJobStart({
    required String jobId,
    String? playlistId,
    String? note,
  }) = _SyncJobStart;

  factory SyncJobStart.fromJson(Map<String, dynamic> json) =>
      _$SyncJobStartFromJson(json);
}

/// Full status snapshot returned by `GET /api/ingest/status/{job_id}` while
/// a Spotify ingest job is running. Buckets sum to `processed`.
@freezed
abstract class SyncJobStatus with _$SyncJobStatus {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory SyncJobStatus({
    required String status,
    @Default(0) int total,
    @Default(0) int processed,
    @Default(0) int addedToLibrary,
    @Default(0) int alreadyInLibrary,
    @Default(0) int queuedForAnalysis,
    @Default(0) int skipped,
    @Default(false) bool cancelRequested,
    String? currentTrack,
    String? errorMessage,
    String? note,
    String? playlistId,
    String? source,
    String? authMode,
    int? userId,
  }) = _SyncJobStatus;

  factory SyncJobStatus.fromJson(Map<String, dynamic> json) =>
      _$SyncJobStatusFromJson(json);
}

/// Request body for `POST /api/ingest/spotify`. `sources` mirrors the
/// backend `IngestRequest.SourcesModel`.
@freezed
abstract class IngestSourcesBody with _$IngestSourcesBody {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory IngestSourcesBody({
    @Default(false) bool liked,
    @Default(false) bool topTracks,
    @Default([]) List<String> playlistIds,
  }) = _IngestSourcesBody;

  factory IngestSourcesBody.fromJson(Map<String, dynamic> json) =>
      _$IngestSourcesBodyFromJson(json);
}
