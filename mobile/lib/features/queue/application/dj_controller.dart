import 'dart:math' as math;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/features/queue/domain/dj_state.dart';

/// Maximum events retained. Older events are dropped FIFO. Matches
/// `DJ_MAX_EVENTS` in `frontend/app.js`.
const int _djMaxEvents = 200;

/// Per-event decay factor. `weight *= _djDecay^age` where age is
/// position-from-newest. Matches `DJ_DECAY` in the web app.
const double _djDecay = 0.94;

/// DJ-mode controller. Owns:
///   * on/off flag,
///   * a bounded event buffer of user interactions,
///   * playback-progress tracking (start timestamp + duration hint so
///     `next` / `skipped` can carry an accurate `played_ratio`),
///   * weighted aggregation to `positives` / `negatives` for the recs call.
class DjController extends Notifier<DjState> {
  @override
  DjState build() => const DjState();

  // ---- toggle + seed ------------------------------------------------------

  void setEnabled(bool on) {
    if (state.enabled == on) return;
    state = state.copyWith(enabled: on, clearSeed: !on);
  }

  void toggle() => setEnabled(!state.enabled);

  void markSeed(String seedId) => state = state.copyWith(lastSeedId: seedId);

  // ---- track lifecycle -----------------------------------------------------

  /// Call when a new track starts playing. Resets the internal
  /// current-track / start-time cursor so the next event can compute an
  /// accurate `playedRatio`.
  void onTrackChanged(String trackId) {
    state = state.copyWith(
      currentTrackId: trackId,
      currentStartMs: DateTime.now().millisecondsSinceEpoch,
    );
  }

  // ---- event recording -----------------------------------------------------

  /// Track played to natural end. Strong positive.
  void recordCompleted(String trackId) => _push(trackId, 'completed');

  /// Explicit "add to queue" — strongest positive.
  void recordQueued(String trackId) => _push(trackId, 'queued');

  /// User hit next. Sign of the signal depends on played ratio.
  ///
  /// [durationMs] is the total track length; used to compute play ratio
  /// vs the current-track start timestamp. Pass 0 to skip weight (event
  /// is still recorded so history is faithful).
  void recordNext(String trackId, {required int durationMs}) =>
      _push(trackId, 'next', ratio: _computeRatio(trackId, durationMs));

  /// User skipped. Negative if played ratio < 0.45.
  void recordSkipped(String trackId, {required int durationMs}) =>
      _push(trackId, 'skipped', ratio: _computeRatio(trackId, durationMs));

  double? _computeRatio(String trackId, int durationMs) {
    if (durationMs <= 0) return null;
    final start = state.currentTrackId == trackId ? state.currentStartMs : null;
    if (start == null) return null;
    final elapsed = DateTime.now().millisecondsSinceEpoch - start;
    return (elapsed / durationMs).clamp(0.0, 1.0);
  }

  void _push(String trackId, String action, {double? ratio}) {
    final evt = DjEvent(
      trackId: trackId,
      action: action,
      playedRatio: ratio,
      ts: DateTime.now().millisecondsSinceEpoch,
    );
    final next = [...state.events, evt];
    while (next.length > _djMaxEvents) {
      next.removeAt(0);
    }
    state = state.copyWith(events: next);
  }

  // ---- taste vector --------------------------------------------------------

  /// Aggregate the event buffer into weighted `positives` + `negatives`
  /// ready to send as `positive_ids` / `negative_ids` on the DJ-mode POST
  /// to `/api/tracks/{key}/similar`. Ported from `djBuildWeights()` in
  /// `frontend/app.js`.
  DjWeights buildWeights() {
    final events = state.events;
    final n = events.length;
    if (n == 0) return const DjWeights(positives: [], negatives: []);

    final pos = <String, double>{};
    final neg = <String, double>{};

    for (var i = 0; i < n; i++) {
      final e = events[i];
      final age = (n - 1) - i;
      final decay = math.pow(_djDecay, age).toDouble();

      double base = 0;
      Map<String, double>? bucket;

      switch (e.action) {
        case 'completed':
          base = 0.8;
          bucket = pos;
        case 'queued':
          base = 1.2;
          bucket = pos;
        case 'next':
          final r = e.playedRatio ?? 0;
          if (r > 0.5) {
            base = 0.3;
            bucket = pos;
          }
        case 'skipped':
          final r = e.playedRatio ?? 0;
          if (r < 0.15) {
            base = 0.8;
            bucket = neg;
          } else if (r < 0.45) {
            base = 0.4;
            bucket = neg;
          }
      }
      if (bucket == null || base == 0) continue;
      final w = base * decay;
      bucket[e.trackId] = (bucket[e.trackId] ?? 0) + w;
    }

    // Resolve conflicts: same id in both piles → whichever weight is larger.
    final positives = <({String id, double weight})>[];
    final negatives = <({String id, double weight})>[];
    final allIds = {...pos.keys, ...neg.keys};
    for (final id in allIds) {
      final p = pos[id] ?? 0;
      final g = neg[id] ?? 0;
      if (p >= g && p > 0) {
        positives.add((id: id, weight: double.parse(p.toStringAsFixed(4))));
      } else if (g > 0) {
        negatives.add((id: id, weight: double.parse(g.toStringAsFixed(4))));
      }
    }
    return DjWeights(positives: positives, negatives: negatives);
  }

  /// Track IDs the recs endpoint should NOT return — the most recent N
  /// completed / next-forwarded tracks. Mirrors `djExcludeIds`.
  List<String> excludeIds({int limit = 30}) {
    final seen = <String>{};
    // Walk newest → oldest, collect forward-move actions.
    for (var i = state.events.length - 1; i >= 0; i--) {
      final e = state.events[i];
      if (e.action == 'completed' || e.action == 'next') {
        seen.add(e.trackId);
        if (seen.length >= limit) break;
      }
    }
    return seen.toList(growable: false);
  }
}

final djControllerProvider =
    NotifierProvider<DjController, DjState>(DjController.new);
