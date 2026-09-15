import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/errors/failure.dart';
import 'package:vibescape/features/queue/application/dj_controller.dart';
import 'package:vibescape/features/queue/application/queue_api.dart';
import 'package:vibescape/features/queue/domain/queue_track.dart';

/// Debounce window before firing a recs request. Keeps rapid track flips
/// (e.g. spamming Next) from thrashing the backend.
const Duration _recsDebounce = Duration(milliseconds: 250);

/// Async list of recommendations for the current seed track. Widgets watch
/// this to render the "Recommended for this track" list.
class RecommendationsController extends AsyncNotifier<List<QueueTrack>> {
  Timer? _debounce;
  String? _pendingSeed;
  int _requestSeq = 0;

  @override
  FutureOr<List<QueueTrack>> build() {
    ref.onDispose(() {
      _debounce?.cancel();
    });
    return const [];
  }

  /// Ask for recs for [seedId]. Debounced — if [seedId] is null the current
  /// list is cleared. If DJ mode is on the taste buffer is included.
  void requestFor(String? seedId) {
    _debounce?.cancel();
    if (seedId == null || seedId.isEmpty) {
      _pendingSeed = null;
      state = const AsyncData([]);
      return;
    }
    _pendingSeed = seedId;
    _debounce = Timer(_recsDebounce, _fire);
  }

  /// Force an immediate refresh, skipping the debounce. Used when DJ mode is
  /// toggled on and we want recs right away.
  Future<void> refreshNow(String seedId) async {
    _debounce?.cancel();
    _pendingSeed = seedId;
    await _fire();
  }

  Future<void> _fire() async {
    final seed = _pendingSeed;
    if (seed == null) return;

    final api = ref.read(queueApiProvider);
    final djCtl = ref.read(djControllerProvider.notifier);
    final dj = ref.read(djControllerProvider);
    final mySeq = ++_requestSeq;

    state = const AsyncLoading<List<QueueTrack>>().copyWithPrevious(state);

    final weights = djCtl.buildWeights();
    final mode = dj.enabled && !weights.isEmpty ? 'dj' : 'vibe';
    final result = await api.fetchRecommendations(
      seed,
      mode: mode,
      positives: weights.positives,
      negatives: weights.negatives,
      excludeIds: djCtl.excludeIds(),
      limit: 20,
    );

    // Drop stale responses — a newer request has already started.
    if (mySeq != _requestSeq) return;

    result.when(
      ok: (list) {
        state = AsyncData(list);
        ref.read(djControllerProvider.notifier).markSeed(seed);
      },
      err: (Failure f) {
        state = AsyncError<List<QueueTrack>>(f, StackTrace.current);
      },
    );
  }

  /// Test hook: current pending seed (before the debounce timer fires).
  String? get debugPendingSeed => _pendingSeed;
}

final recommendationsControllerProvider =
    AsyncNotifierProvider<RecommendationsController, List<QueueTrack>>(
  RecommendationsController.new,
);
