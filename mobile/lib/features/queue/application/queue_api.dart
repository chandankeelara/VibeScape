import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/features/queue/domain/queue_track.dart';

/// API surface the queue + DJ features depend on.
///
/// The wire body of `POST /api/tracks/{trackKey}/similar` accepts
/// `positive_ids` and `negative_ids` as arrays of `{id, weight}` — this
/// interface takes those pre-aggregated (from `DjController.buildWeights`),
/// plus an exclude list, plus the mode flag.
abstract class QueueApi {
  Future<Result<List<QueueTrack>>> fetchRecommendations(
    String trackId, {
    String mode,
    List<({String id, double weight})> positives,
    List<({String id, double weight})> negatives,
    List<String> excludeIds,
    int limit,
  });
}

final queueApiProvider = Provider<QueueApi>((ref) {
  throw UnimplementedError(
    'queueApiProvider must be overridden. Bind it to '
    'VibescapeQueueApiAdapter in lib/core/bootstrap/bootstrap.dart.',
  );
});
