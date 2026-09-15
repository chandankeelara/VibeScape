import 'dart:async';

/// Abstract native-audio interface consumed by the player controller.
///
/// Agent E owns the concrete implementation under
/// `lib/features/native_audio/`. This file declares the local contract so the
/// player feature can compile and be tested independently. If/when
/// `native_audio` lands with its own interface, this can be re-exported or
/// aliased there.
abstract class NativePlayer {
  Stream<NativePlayerState> get stateStream;

  NativePlayerState get currentState;

  Future<void> play(String uri);
  Future<void> pause();
  Future<void> resume();
  Future<void> seek(int positionMs);
  Future<void> stop();
}

/// Snapshot pushed on [NativePlayer.stateStream]. Mirrors what the SDK
/// bridge emits — pure data, no controller logic.
class NativePlayerState {
  const NativePlayerState({
    required this.playing,
    required this.buffering,
    required this.positionMs,
    required this.durationMs,
    this.uri,
    this.error,
  });

  final bool playing;
  final bool buffering;
  final int positionMs;
  final int durationMs;
  final String? uri;
  final String? error;

  static const idle = NativePlayerState(
    playing: false,
    buffering: false,
    positionMs: 0,
    durationMs: 0,
  );

  NativePlayerState copyWith({
    bool? playing,
    bool? buffering,
    int? positionMs,
    int? durationMs,
    String? uri,
    String? error,
  }) {
    return NativePlayerState(
      playing: playing ?? this.playing,
      buffering: buffering ?? this.buffering,
      positionMs: positionMs ?? this.positionMs,
      durationMs: durationMs ?? this.durationMs,
      uri: uri ?? this.uri,
      error: error ?? this.error,
    );
  }
}
