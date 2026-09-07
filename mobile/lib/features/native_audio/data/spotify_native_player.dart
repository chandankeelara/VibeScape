import 'dart:async';

import 'package:spotify_sdk/models/player_state.dart' as sdk;
import 'package:spotify_sdk/spotify_sdk.dart';
import 'package:vibescape/core/errors/failure.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/features/native_audio/data/audio_service_handler.dart';
import 'package:vibescape/features/native_audio/domain/native_player.dart';
import 'package:vibescape/features/native_audio/domain/player_state.dart';
import 'package:vibescape/features/native_audio/domain/remote_command.dart';

/// Real [NativePlayer] backed by the `spotify_sdk` package
/// (Apple SPTAppRemote on iOS, Spotify Android Remote SDK on Android).
///
/// Package exceptions are caught at the method boundary and mapped to
/// [Failure] variants:
/// * "not installed"       -> [Failure.notFound]  (Spotify app missing)
/// * "not premium"         -> [Failure.auth]      (account tier gate)
/// * "auth expired"        -> [Failure.auth]      (token invalid/expired)
/// * "disconnected"        -> [Failure.network]   (app remote lost link)
/// * anything else         -> [Failure.unknown]
class SpotifyNativePlayer implements NativePlayer {
  SpotifyNativePlayer({
    required String clientId,
    required String redirectUrl,
    VibescapeAudioHandler? audioHandler,
  })  : _clientId = clientId,
        _redirectUrl = redirectUrl,
        _audioHandler = audioHandler;

  final String _clientId;
  final String _redirectUrl;
  final VibescapeAudioHandler? _audioHandler;

  final StreamController<PlayerState> _stateController =
      StreamController<PlayerState>.broadcast();

  StreamSubscription<sdk.PlayerState>? _sdkSubscription;
  String? _accessToken;
  bool _isPremium = false;

  @override
  Stream<PlayerState> get stateStream => _stateController.stream;

  @override
  Stream<RemoteCommand> get remoteCommandStream =>
      _audioHandler?.commandStream ?? const Stream<RemoteCommand>.empty();

  @override
  Future<bool> isSpotifyInstalled() async {
    try {
      // spotify_sdk's connectToSpotifyRemote throws "CouldNotFindSpotifyApp"
      // if not installed; there's no dedicated probe API. We treat the
      // absence of a token + a failed connect as "not installed" upstream —
      // this stub is here so callers can pre-check before attempting auth.
      return true;
    } catch (_) {
      return false;
    }
  }

  @override
  Future<Result<void>> authenticate() async {
    try {
      final connected = await SpotifySdk.connectToSpotifyRemote(
        clientId: _clientId,
        redirectUrl: _redirectUrl,
      );
      if (!connected) {
        return const Result.err(
          Failure.auth(message: 'Spotify remote refused connection'),
        );
      }
      _accessToken = await SpotifySdk.getAccessToken(
        clientId: _clientId,
        redirectUrl: _redirectUrl,
        scope:
            'app-remote-control,user-modify-playback-state,user-read-currently-playing,streaming',
      );
      _subscribeToPlayerState();
      return const Result.ok(null);
    } catch (e) {
      return Result.err(_mapException(e));
    }
  }

  void _subscribeToPlayerState() {
    _sdkSubscription?.cancel();
    _sdkSubscription = SpotifySdk.subscribePlayerState().listen(
      (sdkState) {
        _stateController.add(_mapSdkState(sdkState));
      },
      onError: (Object e, StackTrace _) {
        // Surface as an "unknown" state error but keep the stream alive.
        _stateController.addError(_mapException(e));
      },
    );
  }

  PlayerState _mapSdkState(sdk.PlayerState s) {
    final t = s.track;
    final artworkUrl = t?.imageUri.raw;
    return PlayerState(
      uri: t?.uri,
      name: t?.name ?? '',
      artist: t?.artist.name ?? '',
      album: t?.album.name ?? '',
      artworkUrl: artworkUrl,
      positionMs: s.playbackPosition,
      durationMs: t?.duration ?? 0,
      isPaused: s.isPaused,
      isPremium: _isPremium,
    );
  }

  @override
  Future<Result<void>> play(String spotifyUri) async {
    try {
      await SpotifySdk.play(spotifyUri: spotifyUri);
      return const Result.ok(null);
    } catch (e) {
      return Result.err(_mapException(e));
    }
  }

  @override
  Future<Result<void>> pause() async {
    try {
      await SpotifySdk.pause();
      return const Result.ok(null);
    } catch (e) {
      return Result.err(_mapException(e));
    }
  }

  @override
  Future<Result<void>> resume() async {
    try {
      await SpotifySdk.resume();
      return const Result.ok(null);
    } catch (e) {
      return Result.err(_mapException(e));
    }
  }

  @override
  Future<Result<void>> seek(Duration position) async {
    try {
      await SpotifySdk.seekTo(positionedMilliseconds: position.inMilliseconds);
      return const Result.ok(null);
    } catch (e) {
      return Result.err(_mapException(e));
    }
  }

  @override
  Future<Result<void>> disconnect() async {
    try {
      await _sdkSubscription?.cancel();
      _sdkSubscription = null;
      await SpotifySdk.disconnect();
      _accessToken = null;
      return const Result.ok(null);
    } catch (e) {
      return Result.err(_mapException(e));
    }
  }

  /// Map a raw exception from `spotify_sdk` (typically a [PlatformException])
  /// to a [Failure] variant. The SDK's error `code` strings we care about:
  ///
  /// * `CouldNotFindSpotifyApp`   -> not installed
  /// * `NotLoggedInException`     -> auth expired / never signed in
  /// * `AuthenticationFailedException` -> auth expired
  /// * `SpotifyDisconnectedException`  -> app remote link dropped
  /// * `UserNotAuthorizedException`    -> non-premium account
  Failure _mapException(Object e) {
    final msg = e.toString();
    if (msg.contains('CouldNotFindSpotifyApp') ||
        msg.contains('NotFound')) {
      return const Failure.notFound(
        message: 'Spotify app is not installed on this device',
      );
    }
    if (msg.contains('UserNotAuthorized') || msg.contains('PREMIUM')) {
      return const Failure.auth(
        message: 'Spotify Premium is required for full-track playback',
      );
    }
    if (msg.contains('NotLoggedIn') ||
        msg.contains('AuthenticationFailed') ||
        msg.contains('AuthenticationException')) {
      return const Failure.auth(message: 'Spotify auth expired — sign in again');
    }
    if (msg.contains('Disconnected') || msg.contains('CouldNotConnect')) {
      return const Failure.network(
        message: 'Lost connection to the Spotify app',
      );
    }
    return Failure.unknown(message: 'Spotify SDK error: $msg', cause: e);
  }

  Future<void> dispose() async {
    await _sdkSubscription?.cancel();
    await _stateController.close();
  }
}
