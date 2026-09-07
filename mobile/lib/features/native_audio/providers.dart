import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/features/native_audio/domain/native_player.dart';
import 'package:vibescape/features/native_audio/domain/player_state.dart';
import 'package:vibescape/features/native_audio/domain/remote_command.dart';

// Barrel re-exports so callers only need to import providers.dart.
export 'package:vibescape/features/native_audio/data/audio_service_handler.dart';
export 'package:vibescape/features/native_audio/data/fake_native_player.dart';
export 'package:vibescape/features/native_audio/data/spotify_native_player.dart';
export 'package:vibescape/features/native_audio/domain/native_player.dart';
export 'package:vibescape/features/native_audio/domain/player_state.dart';
export 'package:vibescape/features/native_audio/domain/remote_command.dart';

/// Riverpod handle to the native audio bridge.
///
/// **MUST be overridden at app root** (see `main.dart` note in README).
/// Overriding with a [FakeNativePlayer] is the recommended way to write
/// widget / controller tests against this feature.
final nativePlayerProvider = Provider<NativePlayer>((ref) {
  throw UnimplementedError(
    'nativePlayerProvider must be overridden at ProviderScope. '
    'See lib/features/native_audio/README.md for wire-up details.',
  );
});

/// Live [PlayerState] stream from the bridge — thin passthrough so widgets
/// can `ref.watch(playerStateStreamProvider)` and get an AsyncValue.
final playerStateStreamProvider = StreamProvider<PlayerState>((ref) {
  return ref.watch(nativePlayerProvider).stateStream;
});

/// Live [RemoteCommand] stream (lockscreen / bluetooth transport buttons).
final remoteCommandStreamProvider = StreamProvider<RemoteCommand>((ref) {
  return ref.watch(nativePlayerProvider).remoteCommandStream;
});
