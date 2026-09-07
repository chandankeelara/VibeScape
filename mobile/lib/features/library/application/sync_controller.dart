import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/errors/failure.dart';
import 'package:vibescape/features/library/application/library_api.dart';
import 'package:vibescape/features/library/domain/sync_selection.dart';

/// Aggregate state shown in the modal. Idle → loadingPlaylists →
/// awaitingSelection → running → complete / error.
enum SyncPhase { idle, loadingPlaylists, awaitingSelection, running, complete }

@immutable
class SyncControllerState {
  const SyncControllerState({
    this.phase = SyncPhase.idle,
    this.playlists = const [],
    this.selection = const SyncSelection(),
    this.progress,
    this.jobId,
    this.error,
  });

  final SyncPhase phase;
  final List<SyncPlaylist> playlists;
  final SyncSelection selection;
  final SyncProgress? progress;
  final String? jobId;
  final String? error;

  SyncControllerState copyWith({
    SyncPhase? phase,
    List<SyncPlaylist>? playlists,
    SyncSelection? selection,
    SyncProgress? progress,
    String? jobId,
    String? error,
    bool clearError = false,
    bool clearJobId = false,
    bool clearProgress = false,
  }) {
    return SyncControllerState(
      phase: phase ?? this.phase,
      playlists: playlists ?? this.playlists,
      selection: selection ?? this.selection,
      progress: clearProgress ? null : (progress ?? this.progress),
      jobId: clearJobId ? null : (jobId ?? this.jobId),
      error: clearError ? null : (error ?? this.error),
    );
  }
}

class SyncController extends Notifier<SyncControllerState> {
  StreamSubscription<SyncProgress>? _sub;

  @override
  SyncControllerState build() {
    ref.onDispose(() => _sub?.cancel());
    return const SyncControllerState();
  }

  Future<void> loadPlaylists() async {
    state = state.copyWith(phase: SyncPhase.loadingPlaylists, clearError: true);
    final res = await ref.read(libraryApiProvider).listPlaylists();
    res.when(
      ok: (list) => state = state.copyWith(
        playlists: list,
        phase: SyncPhase.awaitingSelection,
      ),
      err: (Failure f) => state = state.copyWith(
        phase: SyncPhase.idle,
        error: _messageOf(f),
      ),
    );
  }

  static String _messageOf(Failure f) => switch (f) {
        NetworkFailure(:final message) => message,
        AuthFailure(:final message) => message,
        NotFoundFailure(:final message) => message,
        ValidationFailure(:final message) => message,
        UnknownFailure(:final message) => message,
      };

  void togglePlaylist(String id, bool checked) {
    final ids = {...state.selection.playlistIds};
    if (checked) {
      ids.add(id);
    } else {
      ids.remove(id);
    }
    state = state.copyWith(selection: state.selection.copyWith(playlistIds: ids));
  }

  void setIncludeLiked(bool v) {
    state = state.copyWith(selection: state.selection.copyWith(includeLiked: v));
  }

  void setIncludeTop(bool v) {
    state = state.copyWith(selection: state.selection.copyWith(includeTop: v));
  }

  void setPublicPlaylistUrl(String? url) {
    state = state.copyWith(
      selection: state.selection.copyWith(
        publicPlaylistUrl: url,
        clearUrl: url == null || url.isEmpty,
      ),
    );
  }

  Future<void> startSync() async {
    if (state.selection.isEmpty) return;
    state = state.copyWith(
      phase: SyncPhase.running,
      clearError: true,
      clearProgress: true,
    );
    final res = await ref.read(libraryApiProvider).startSync(state.selection);
    res.when(
      ok: (jobId) {
        state = state.copyWith(jobId: jobId);
        _subscribe(jobId);
      },
      err: (Failure f) => state = state.copyWith(
        phase: SyncPhase.awaitingSelection,
        error: _messageOf(f),
      ),
    );
  }

  void _subscribe(String jobId) {
    _sub?.cancel();
    _sub = ref.read(libraryApiProvider).syncJobStream(jobId).listen(
      (p) {
        state = state.copyWith(progress: p);
        if (p.finished) {
          state = state.copyWith(phase: SyncPhase.complete);
          _sub?.cancel();
          _sub = null;
        }
      },
      onError: (Object e, StackTrace st) {
        state = state.copyWith(
          phase: SyncPhase.awaitingSelection,
          error: 'Sync stream failed: $e',
        );
      },
    );
  }

  /// Reset the controller back to idle. Cancels any live subscription.
  void reset() {
    _sub?.cancel();
    _sub = null;
    state = const SyncControllerState();
  }
}

final syncControllerProvider =
    NotifierProvider<SyncController, SyncControllerState>(SyncController.new);
