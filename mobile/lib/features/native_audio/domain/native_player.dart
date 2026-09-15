import 'dart:async';

import 'package:vibescape/core/errors/failure.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/features/native_audio/domain/player_state.dart';
import 'package:vibescape/features/native_audio/domain/remote_command.dart';

/// Native audio bridge — the only surface the rest of the app uses to control
/// Spotify (via `spotify_sdk`) or the local preview fallback (via
/// `just_audio`). Concrete implementations live in `data/`.
///
/// All fallible operations return [Result] — implementations MUST catch
/// package exceptions at the boundary and map them to a [Failure] variant.
abstract class NativePlayer {
  /// Initialises SPTSessionManager (iOS) / SpotifyAppRemote (Android) and
  /// obtains an access token. Idempotent — safe to call on resume.
  Future<Result<void>> authenticate();

  /// Starts playback of a Spotify URI (`spotify:track:...`,
  /// `spotify:playlist:...`, etc.).
  Future<Result<void>> play(String spotifyUri);

  Future<Result<void>> pause();
  Future<Result<void>> resume();
  Future<Result<void>> seek(Duration position);

  /// Tears down the Spotify session + audio_service handler.
  Future<Result<void>> disconnect();

  /// Whether the Spotify app is installed on-device. On iOS this checks the
  /// URL scheme; on Android it inspects the package manager. Non-throwing.
  Future<bool> isSpotifyInstalled();

  /// Broadcast stream of [PlayerState] snapshots. Backed by
  /// `SpotifySdk.subscribePlayerState()` in the real implementation.
  Stream<PlayerState> get stateStream;

  /// Broadcast stream of [RemoteCommand] events from the lockscreen /
  /// bluetooth headset (routed through `audio_service`).
  Stream<RemoteCommand> get remoteCommandStream;
}
