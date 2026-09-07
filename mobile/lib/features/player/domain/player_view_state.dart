import 'package:freezed_annotation/freezed_annotation.dart';

part 'player_view_state.freezed.dart';

/// Lightweight track representation the player screen needs. Full track model
/// lives in the tracks feature; this is the projection consumed by the player.
@freezed
abstract class PlayerTrack with _$PlayerTrack {
  const factory PlayerTrack({
    required String id,
    required String title,
    required String artist,
    String? album,
    String? artworkUrl,
    String? previewUrl,
    String? source, // 'spotify', 'preview', 'youtube', ...
    String? genre,
    String? language,
    int? vibe, // predicted 0-100
    @Default(30000) int durationMs,
  }) = _PlayerTrack;
}

enum PlaybackStatus { idle, loading, playing, paused, ended, error }

@freezed
abstract class PlayerViewState with _$PlayerViewState {
  const factory PlayerViewState({
    PlayerTrack? track,
    @Default(PlaybackStatus.idle) PlaybackStatus status,
    @Default(0) int positionMs,
    @Default(0) int durationMs,
    @Default(false) bool isScrubbing,
    String? errorMessage,
  }) = _PlayerViewState;

  const PlayerViewState._();

  static const empty = PlayerViewState();

  bool get isPlaying => status == PlaybackStatus.playing;
}
