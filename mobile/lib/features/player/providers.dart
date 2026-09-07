/// Barrel of public providers exported by the player feature.
///
/// External code (router, other features) should import from here — never
/// reach into `application/` or `domain/` directly.
library;

export 'application/player_controller.dart'
    show playerControllerProvider, nativePlayerProvider, vibescapeApiProvider;
export 'application/vibe_controller.dart' show vibeControllerProvider;
export 'domain/native_player.dart' show NativePlayer, NativePlayerState;
export 'domain/player_view_state.dart'
    show PlayerViewState, PlayerTrack, PlaybackStatus;
export 'domain/vibescape_api.dart' show VibescapeApi;
