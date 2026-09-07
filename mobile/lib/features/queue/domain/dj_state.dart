import 'package:flutter/foundation.dart';

/// A single DJ event captured by the controller — mirrors the shape written
/// by `djPushEvent()` in `frontend/app.js`.
///
/// `action`:
///   * 'completed' — track played to natural end (strong positive)
///   * 'queued'    — user explicitly added to queue (strongest positive)
///   * 'next'      — user hit next; positive if `playedRatio > 0.5`
///   * 'skipped'   — user skipped; negative if `playedRatio < 0.45`
///
/// `playedRatio` is the fraction of the track that had been playing when the
/// action fired (0..1). Required for `next` and `skipped` actions.
@immutable
class DjEvent {
  const DjEvent({
    required this.trackId,
    required this.action,
    this.playedRatio,
    required this.ts,
  });

  factory DjEvent.fromJson(Map<String, dynamic> j) => DjEvent(
        trackId: j['track_id'] as String,
        action: j['action'] as String,
        playedRatio: (j['played_ratio'] as num?)?.toDouble(),
        ts: (j['ts'] as num).toInt(),
      );

  final String trackId;
  final String action;
  final double? playedRatio;
  final int ts;

  Map<String, dynamic> toJson() => {
        'track_id': trackId,
        'action': action,
        'played_ratio': playedRatio,
        'ts': ts,
      };
}

/// Aggregation output of [DjController.buildWeights]. Ready to send as
/// `positive_ids` / `negative_ids` to `POST /api/tracks/{key}/similar`.
@immutable
class DjWeights {
  const DjWeights({required this.positives, required this.negatives});
  final List<({String id, double weight})> positives;
  final List<({String id, double weight})> negatives;

  bool get isEmpty => positives.isEmpty && negatives.isEmpty;
}

@immutable
class DjState {
  const DjState({
    this.enabled = false,
    this.events = const [],
    this.currentTrackId,
    this.currentStartMs,
    this.lastSeedId,
  });

  final bool enabled;

  /// Bounded event buffer, oldest first. See [DjEvent].
  final List<DjEvent> events;

  /// Currently-playing track id (for tracking play ratio).
  final String? currentTrackId;

  /// Wall-clock ms when [currentTrackId] started playing.
  final int? currentStartMs;

  /// Seed id used for the last recommendations fetch — dedupes rapid refetches.
  final String? lastSeedId;

  DjState copyWith({
    bool? enabled,
    List<DjEvent>? events,
    String? currentTrackId,
    int? currentStartMs,
    String? lastSeedId,
    bool clearSeed = false,
    bool clearCurrent = false,
  }) {
    return DjState(
      enabled: enabled ?? this.enabled,
      events: events ?? this.events,
      currentTrackId:
          clearCurrent ? null : (currentTrackId ?? this.currentTrackId),
      currentStartMs:
          clearCurrent ? null : (currentStartMs ?? this.currentStartMs),
      lastSeedId: clearSeed ? null : (lastSeedId ?? this.lastSeedId),
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is DjState &&
          other.enabled == enabled &&
          listEquals(other.events, events) &&
          other.currentTrackId == currentTrackId &&
          other.currentStartMs == currentStartMs &&
          other.lastSeedId == lastSeedId);

  @override
  int get hashCode => Object.hash(
        enabled,
        Object.hashAll(events),
        currentTrackId,
        currentStartMs,
        lastSeedId,
      );
}
