import 'package:freezed_annotation/freezed_annotation.dart';

part 'health.freezed.dart';
part 'health.g.dart';

/// Response for `GET /api/health` — a tiny liveness ping with a track-count
/// summary the landing page uses to warn about empty libraries.
@freezed
abstract class HealthStatus with _$HealthStatus {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory HealthStatus({
    required String status,
    @Default(0) int trackCount,
  }) = _HealthStatus;

  factory HealthStatus.fromJson(Map<String, dynamic> json) =>
      _$HealthStatusFromJson(json);
}
