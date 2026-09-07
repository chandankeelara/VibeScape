import 'package:flutter/foundation.dart';
import 'package:vibescape/features/queue/domain/queue_track.dart';

/// State of the user-managed play queue. `tracks` is the ordered "up next"
/// list. `currentId` is the id of whatever is playing now (owned elsewhere)
/// and is stored here so the queue can visually mark it.
@immutable
class QueueState {
  const QueueState({
    this.tracks = const [],
    this.currentId,
  });

  final List<QueueTrack> tracks;
  final String? currentId;

  int get length => tracks.length;
  bool get isEmpty => tracks.isEmpty;

  QueueState copyWith({
    List<QueueTrack>? tracks,
    String? currentId,
    bool clearCurrent = false,
  }) {
    return QueueState(
      tracks: tracks ?? this.tracks,
      currentId: clearCurrent ? null : (currentId ?? this.currentId),
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is QueueState &&
          listEquals(other.tracks, tracks) &&
          other.currentId == currentId);

  @override
  int get hashCode => Object.hash(Object.hashAll(tracks), currentId);
}
