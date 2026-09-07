import 'package:freezed_annotation/freezed_annotation.dart';

part 'player_state.freezed.dart';

/// Snapshot of native audio playback state emitted by [NativePlayer].
///
/// Mirrors the fields VibeScape's web client reads from the Spotify Web
/// Playback SDK's `player_state_changed` event (see `frontend/app.js`), plus
/// a few normalised fields we want on-device (artwork URL, premium flag).
@freezed
abstract class PlayerState with _$PlayerState {
  const factory PlayerState({
    /// Spotify URI currently loaded (e.g. `spotify:track:xyz`).
    /// `null` when nothing is loaded / player disconnected.
    String? uri,
    @Default('') String name,
    @Default('') String artist,
    @Default('') String album,
    String? artworkUrl,
    @Default(0) int positionMs,
    @Default(0) int durationMs,
    @Default(true) bool isPaused,
    @Default(false) bool isPremium,
  }) = _PlayerState;

  /// Idle / disconnected state — nothing loaded, paused.
  static const PlayerState idle = PlayerState();
}
