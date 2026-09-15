import 'dart:async';

import 'package:vibescape/core/errors/failure.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/features/player/providers.dart';

class FakeNativePlayer implements NativePlayer {
  FakeNativePlayer();

  final _controller = StreamController<NativePlayerState>.broadcast();
  NativePlayerState _state = NativePlayerState.idle;

  final List<String> playCalls = <String>[];
  int pauseCalls = 0;
  int resumeCalls = 0;
  final List<int> seekCalls = <int>[];
  int stopCalls = 0;

  void emit(NativePlayerState s) {
    _state = s;
    _controller.add(s);
  }

  @override
  Stream<NativePlayerState> get stateStream => _controller.stream;

  @override
  NativePlayerState get currentState => _state;

  @override
  Future<void> play(String uri) async {
    playCalls.add(uri);
    emit(_state.copyWith(playing: true, uri: uri));
  }

  @override
  Future<void> pause() async {
    pauseCalls += 1;
    emit(_state.copyWith(playing: false));
  }

  @override
  Future<void> resume() async {
    resumeCalls += 1;
    emit(_state.copyWith(playing: true));
  }

  @override
  Future<void> seek(int positionMs) async {
    seekCalls.add(positionMs);
    emit(_state.copyWith(positionMs: positionMs));
  }

  @override
  Future<void> stop() async {
    stopCalls += 1;
    emit(NativePlayerState.idle);
  }

  Future<void> dispose() => _controller.close();
}

class FakeVibescapeApi implements VibescapeApi {
  FakeVibescapeApi({this.nextResult});

  Result<PlayerTrack>? nextResult;
  final List<int> nextCalls = <int>[];

  @override
  Future<Result<PlayerTrack>> nextTrackForVibe(int vibe) async {
    nextCalls.add(vibe);
    return nextResult ??
        Result<PlayerTrack>.err(
          const Failure.unknown(message: 'no track'),
        );
  }
}

PlayerTrack sampleTrack({String id = 't1', int vibe = 50}) => PlayerTrack(
      id: id,
      title: 'Sample $id',
      artist: 'Sample artist',
      album: 'Sample album',
      artworkUrl: null,
      previewUrl: 'https://example.com/$id.mp3',
      source: 'preview',
      vibe: vibe,
      durationMs: 30000,
    );
