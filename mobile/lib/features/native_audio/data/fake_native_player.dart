import 'dart:async';

import 'package:vibescape/core/errors/failure.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/features/native_audio/domain/native_player.dart';
import 'package:vibescape/features/native_audio/domain/player_state.dart';
import 'package:vibescape/features/native_audio/domain/remote_command.dart';

/// In-memory fake used by tests. Exposes the underlying [StreamController]s
/// so tests can push arbitrary state / remote-command events.
class FakeNativePlayer implements NativePlayer {
  FakeNativePlayer({
    bool spotifyInstalled = true,
    bool authenticated = false,
  })  : _spotifyInstalled = spotifyInstalled,
        _authenticated = authenticated;

  /// Push arbitrary [PlayerState] events by adding to this controller.
  final StreamController<PlayerState> stateController =
      StreamController<PlayerState>.broadcast();

  /// Push arbitrary [RemoteCommand] events by adding to this controller.
  final StreamController<RemoteCommand> remoteCommandController =
      StreamController<RemoteCommand>.broadcast();

  /// Ordered log of method calls for assertion in tests.
  final List<String> calls = <String>[];

  /// If set, the next method invocation returns this failure once and clears.
  Failure? nextFailure;

  bool _spotifyInstalled;
  bool _authenticated;
  PlayerState _current = PlayerState.idle;

  // ignore: use_setters_to_change_properties
  void setSpotifyInstalled({required bool installed}) {
    _spotifyInstalled = installed;
  }

  PlayerState get currentState => _current;

  Result<T> _consumeFailureOr<T>(T value) {
    final f = nextFailure;
    if (f != null) {
      nextFailure = null;
      return Result.err(f);
    }
    return Result.ok(value);
  }

  @override
  Future<Result<void>> authenticate() async {
    calls.add('authenticate');
    final r = _consumeFailureOr<void>(null);
    if (r.isOk) _authenticated = true;
    return r;
  }

  @override
  Future<Result<void>> play(String spotifyUri) async {
    calls.add('play:$spotifyUri');
    if (!_authenticated) {
      return const Result.err(Failure.auth(message: 'not authenticated'));
    }
    final r = _consumeFailureOr<void>(null);
    if (r.isOk) {
      _current = _current.copyWith(uri: spotifyUri, isPaused: false);
      stateController.add(_current);
    }
    return r;
  }

  @override
  Future<Result<void>> pause() async {
    calls.add('pause');
    final r = _consumeFailureOr<void>(null);
    if (r.isOk) {
      _current = _current.copyWith(isPaused: true);
      stateController.add(_current);
    }
    return r;
  }

  @override
  Future<Result<void>> resume() async {
    calls.add('resume');
    final r = _consumeFailureOr<void>(null);
    if (r.isOk) {
      _current = _current.copyWith(isPaused: false);
      stateController.add(_current);
    }
    return r;
  }

  @override
  Future<Result<void>> seek(Duration position) async {
    calls.add('seek:${position.inMilliseconds}');
    final r = _consumeFailureOr<void>(null);
    if (r.isOk) {
      _current = _current.copyWith(positionMs: position.inMilliseconds);
      stateController.add(_current);
    }
    return r;
  }

  @override
  Future<Result<void>> disconnect() async {
    calls.add('disconnect');
    _authenticated = false;
    _current = PlayerState.idle;
    stateController.add(_current);
    return const Result.ok(null);
  }

  @override
  Future<bool> isSpotifyInstalled() async => _spotifyInstalled;

  @override
  Stream<PlayerState> get stateStream => stateController.stream;

  @override
  Stream<RemoteCommand> get remoteCommandStream =>
      remoteCommandController.stream;

  Future<void> dispose() async {
    await stateController.close();
    await remoteCommandController.close();
  }
}
