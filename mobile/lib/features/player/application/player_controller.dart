import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/features/player/domain/native_player.dart';
import 'package:vibescape/features/player/domain/player_view_state.dart';
import 'package:vibescape/features/player/domain/vibescape_api.dart';

/// Providers here are declared with an `UnimplementedError` fallback because
/// the concrete implementations live in sibling features (Agent A: api,
/// Agent E: native_audio). The app-composition root (or tests) overrides
/// them with real instances. See `providers.dart` for the barrel export.
final nativePlayerProvider = Provider<NativePlayer>((ref) {
  throw UnimplementedError(
    'nativePlayerProvider must be overridden — Agent E supplies the impl.',
  );
});

final vibescapeApiProvider = Provider<VibescapeApi>((ref) {
  throw UnimplementedError(
    'vibescapeApiProvider must be overridden — Agent A supplies the impl.',
  );
});

/// Controller for the now-playing screen. Subscribes to the native player's
/// state stream and exposes intents (play, pause, next, prev, seek, setVibe).
///
/// The controller does not itself track the vibe value — that lives in
/// `vibeControllerProvider` — but it exposes `next()` which reads the current
/// vibe and requests a new recommendation.
class PlayerController extends AsyncNotifier<PlayerViewState> {
  StreamSubscription<NativePlayerState>? _sub;
  final List<PlayerTrack> _history = <PlayerTrack>[];
  int _historyIndex = -1;

  @override
  FutureOr<PlayerViewState> build() {
    final player = ref.watch(nativePlayerProvider);
    _sub?.cancel();
    _sub = player.stateStream.listen(_onNativeState);
    ref.onDispose(() => _sub?.cancel());
    return PlayerViewState.empty;
  }

  void _onNativeState(NativePlayerState s) {
    final prev = state.valueOrNull ?? PlayerViewState.empty;
    // Decouple position updates while user is scrubbing.
    final position = prev.isScrubbing ? prev.positionMs : s.positionMs;
    final status = _mapStatus(s);
    state = AsyncData(
      prev.copyWith(
        status: status,
        positionMs: position,
        durationMs: s.durationMs > 0 ? s.durationMs : prev.durationMs,
        errorMessage: s.error,
      ),
    );
  }

  PlaybackStatus _mapStatus(NativePlayerState s) {
    if (s.error != null) return PlaybackStatus.error;
    if (s.buffering) return PlaybackStatus.loading;
    if (s.playing) return PlaybackStatus.playing;
    if (s.durationMs > 0 && s.positionMs >= s.durationMs) return PlaybackStatus.ended;
    return PlaybackStatus.paused;
  }

  Future<void> loadAndPlay(PlayerTrack track) async {
    final prev = state.valueOrNull ?? PlayerViewState.empty;
    state = AsyncData(
      prev.copyWith(
        track: track,
        status: PlaybackStatus.loading,
        positionMs: 0,
        durationMs: track.durationMs,
      ),
    );
    if (_historyIndex == -1 || _history.isEmpty || _history.last.id != track.id) {
      // Truncate forward history when a new track is loaded off-branch.
      if (_historyIndex >= 0 && _historyIndex < _history.length - 1) {
        _history.removeRange(_historyIndex + 1, _history.length);
      }
      _history.add(track);
      _historyIndex = _history.length - 1;
    }
    final uri = track.previewUrl ?? 'spotify:track:${track.id}';
    await ref.read(nativePlayerProvider).play(uri);
  }

  Future<void> play() async {
    final current = state.valueOrNull;
    if (current?.track == null) return;
    await ref.read(nativePlayerProvider).resume();
  }

  Future<void> pause() async {
    await ref.read(nativePlayerProvider).pause();
  }

  Future<void> togglePlayPause() async {
    final s = state.valueOrNull;
    if (s == null || s.track == null) return;
    if (s.isPlaying) {
      await pause();
    } else {
      await play();
    }
  }

  Future<void> next({int vibe = 50}) async {
    // Fast-forward within history if we have a next.
    if (_historyIndex + 1 < _history.length) {
      _historyIndex += 1;
      await loadAndPlay(_history[_historyIndex]);
      return;
    }
    final result = await ref.read(vibescapeApiProvider).nextTrackForVibe(vibe);
    await result.when(
      ok: loadAndPlay,
      err: (f) async {
        state = AsyncData(
          (state.valueOrNull ?? PlayerViewState.empty)
              .copyWith(errorMessage: f.toString()),
        );
      },
    );
  }

  Future<void> previous() async {
    if (_historyIndex <= 0) {
      await seek(0);
      return;
    }
    _historyIndex -= 1;
    await loadAndPlay(_history[_historyIndex]);
  }

  void beginScrub() {
    final prev = state.valueOrNull ?? PlayerViewState.empty;
    state = AsyncData(prev.copyWith(isScrubbing: true));
  }

  void updateScrubPosition(int positionMs) {
    final prev = state.valueOrNull ?? PlayerViewState.empty;
    if (!prev.isScrubbing) return;
    state = AsyncData(prev.copyWith(positionMs: positionMs));
  }

  Future<void> endScrub(int positionMs) async {
    final prev = state.valueOrNull ?? PlayerViewState.empty;
    state = AsyncData(
      prev.copyWith(isScrubbing: false, positionMs: positionMs),
    );
    await seek(positionMs);
  }

  Future<void> seek(int positionMs) async {
    await ref.read(nativePlayerProvider).seek(positionMs);
  }
}

final playerControllerProvider =
    AsyncNotifierProvider<PlayerController, PlayerViewState>(PlayerController.new);
