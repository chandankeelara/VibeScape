import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/features/queue/application/queue_api.dart';
import 'package:vibescape/features/queue/domain/queue_track.dart';

/// Minimal fake for widget tests that need to render the queue sheet.
/// Exposes the calls it received so tests can assert wiring.
class FakeQueueApi implements QueueApi {
  FakeQueueApi({this.result = const <QueueTrack>[]});

  final List<QueueTrack> result;

  String? lastSeed;
  String? lastMode;
  List<({String id, double weight})> lastPositives = const [];
  List<({String id, double weight})> lastNegatives = const [];
  List<String> lastExcludeIds = const [];

  @override
  Future<Result<List<QueueTrack>>> fetchRecommendations(
    String trackId, {
    String mode = 'vibe',
    List<({String id, double weight})> positives = const [],
    List<({String id, double weight})> negatives = const [],
    List<String> excludeIds = const [],
    int limit = 20,
  }) async {
    lastSeed = trackId;
    lastMode = mode;
    lastPositives = positives;
    lastNegatives = negatives;
    lastExcludeIds = excludeIds;
    return Result.ok(result);
  }
}
