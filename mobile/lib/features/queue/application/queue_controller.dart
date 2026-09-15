import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/features/queue/domain/queue_state.dart';
import 'package:vibescape/features/queue/domain/queue_track.dart';

/// Owns the user-managed "Up next" list. Mirrors web `state.queue` and the
/// `renderQueue` / addToQueue / removeFromQueue helpers in frontend/app.js.
class QueueController extends Notifier<QueueState> {
  @override
  QueueState build() => const QueueState();

  /// Append a track at the end of the queue. No-op if the exact same id is
  /// already present as the last entry (matches web dedupe).
  void add(QueueTrack track) {
    final tracks = state.tracks;
    if (tracks.isNotEmpty && tracks.last.id == track.id) return;
    state = state.copyWith(tracks: [...tracks, track]);
  }

  /// Insert a track at a specific index. Clamps to valid range.
  void insertAt(int index, QueueTrack track) {
    final tracks = [...state.tracks];
    final i = index.clamp(0, tracks.length);
    tracks.insert(i, track);
    state = state.copyWith(tracks: tracks);
  }

  /// Remove the track at [index]. No-op if out of range.
  void removeAt(int index) {
    if (index < 0 || index >= state.tracks.length) return;
    final tracks = [...state.tracks]..removeAt(index);
    state = state.copyWith(tracks: tracks);
  }

  /// ReorderableListView-style reorder. Adjusts the destination index the
  /// same way Flutter's `onReorder` callback expects.
  void reorder(int oldIndex, int newIndex) {
    if (oldIndex < 0 || oldIndex >= state.tracks.length) return;
    final tracks = [...state.tracks];
    final target = newIndex > oldIndex ? newIndex - 1 : newIndex;
    final item = tracks.removeAt(oldIndex);
    tracks.insert(target.clamp(0, tracks.length), item);
    state = state.copyWith(tracks: tracks);
  }

  /// Drop everything.
  void clear() {
    state = state.copyWith(tracks: const [], clearCurrent: true);
  }

  /// Pop and return the next track (used by the player when advancing).
  QueueTrack? advanceNext() {
    if (state.tracks.isEmpty) return null;
    final next = state.tracks.first;
    state = state.copyWith(
      tracks: state.tracks.sublist(1),
      currentId: next.id,
    );
    return next;
  }

  /// Jump to a specific queue index — moves everything before it into the
  /// past and marks that track as current.
  QueueTrack? jumpTo(int index) {
    if (index < 0 || index >= state.tracks.length) return null;
    final target = state.tracks[index];
    state = state.copyWith(
      tracks: state.tracks.sublist(index + 1),
      currentId: target.id,
    );
    return target;
  }

  /// Update the "now playing" id (does not touch the queue itself).
  void setCurrent(String? id) {
    state = state.copyWith(currentId: id, clearCurrent: id == null);
  }
}

final queueControllerProvider =
    NotifierProvider<QueueController, QueueState>(QueueController.new);
