import 'dart:async';

import 'package:audio_service/audio_service.dart';
import 'package:vibescape/features/native_audio/domain/remote_command.dart';

/// Bridges `audio_service`'s [BaseAudioHandler] lifecycle to a broadcast
/// stream of [RemoteCommand] events. The lockscreen / MPRemoteCommandCenter
/// / bluetooth-headset buttons all funnel through the `play` / `pause` /
/// `skipToNext` / `skipToPrevious` methods below; we translate each into a
/// [RemoteCommand] and re-emit on [commandStream].
///
/// The real playback (Spotify SDK) is driven by
/// `SpotifyNativePlayer` — this handler is purely for OS-level transport
/// integration + `MediaItem` metadata for the lockscreen.
class VibescapeAudioHandler extends BaseAudioHandler {
  VibescapeAudioHandler();

  final StreamController<RemoteCommand> _commandController =
      StreamController<RemoteCommand>.broadcast();

  /// Broadcast stream of remote-transport events.
  Stream<RemoteCommand> get commandStream => _commandController.stream;

  /// Push metadata to the lockscreen. Call this from the player controller
  /// each time the current track changes.
  Future<void> setNowPlaying({
    required String id,
    required String title,
    required String artist,
    String? album,
    String? artworkUrl,
    Duration? duration,
  }) async {
    mediaItem.add(
      MediaItem(
        id: id,
        title: title,
        artist: artist,
        album: album,
        artUri: artworkUrl != null ? Uri.tryParse(artworkUrl) : null,
        duration: duration,
      ),
    );
  }

  /// Push playback state (playing/paused + position) to the lockscreen.
  Future<void> setPlaybackState({
    required bool playing,
    required Duration position,
    Duration bufferedPosition = Duration.zero,
  }) async {
    playbackState.add(
      PlaybackState(
        controls: <MediaControl>[
          MediaControl.skipToPrevious,
          if (playing) MediaControl.pause else MediaControl.play,
          MediaControl.skipToNext,
        ],
        systemActions: const <MediaAction>{
          MediaAction.seek,
        },
        androidCompactActionIndices: const <int>[0, 1, 2],
        processingState: AudioProcessingState.ready,
        playing: playing,
        updatePosition: position,
        bufferedPosition: bufferedPosition,
      ),
    );
  }

  @override
  Future<void> play() async {
    _commandController.add(const PlayCommand());
  }

  @override
  Future<void> pause() async {
    _commandController.add(const PauseCommand());
  }

  @override
  Future<void> skipToNext() async {
    _commandController.add(const NextCommand());
  }

  @override
  Future<void> skipToPrevious() async {
    _commandController.add(const PreviousCommand());
  }

  @override
  Future<void> stop() async {
    await super.stop();
  }

  /// Test-only: emit a command directly. Real code goes through the
  /// [BaseAudioHandler] overrides above.
  void debugEmit(RemoteCommand command) {
    _commandController.add(command);
  }

  Future<void> dispose() async {
    await _commandController.close();
  }
}
