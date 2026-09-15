/// Adapts the native_audio feature's `NativePlayer` (Result-based, freezed
/// state) to the player feature's local `NativePlayer` interface (void-based,
/// simple state). Both features declared their own interfaces per
/// ARCHITECTURE.md — this bridge is the wire-up seam.
library;

import 'package:vibescape/features/native_audio/providers.dart' as native;
import 'package:vibescape/features/player/providers.dart' as player;

class NativePlayerBridge implements player.NativePlayer {
  NativePlayerBridge(this._inner);
  final native.NativePlayer _inner;

  @override
  Stream<player.NativePlayerState> get stateStream => _inner.stateStream.map(_map);

  @override
  player.NativePlayerState get currentState => const player.NativePlayerState(
        playing: false,
        buffering: false,
        positionMs: 0,
        durationMs: 0,
      );

  @override
  Future<void> play(String uri) async {
    await _inner.play(uri);
  }

  @override
  Future<void> pause() async {
    await _inner.pause();
  }

  @override
  Future<void> resume() async {
    await _inner.resume();
  }

  @override
  Future<void> seek(int positionMs) async {
    await _inner.seek(Duration(milliseconds: positionMs));
  }

  @override
  Future<void> stop() async {
    await _inner.disconnect();
  }

  player.NativePlayerState _map(native.PlayerState s) => player.NativePlayerState(
        playing: !s.isPaused,
        buffering: false,
        positionMs: s.positionMs,
        durationMs: s.durationMs,
        uri: s.uri,
      );
}
