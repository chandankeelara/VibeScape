/// One-stop wire-up of all feature providers to their concrete
/// implementations. `main.dart` calls [productionOverrides]; tests call
/// [testOverrides] to inject fakes.
library;

import 'package:audio_service/audio_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/api/vibescape_api.dart';
import 'package:vibescape/core/bootstrap/api_adapters.dart';
import 'package:vibescape/core/bootstrap/native_player_bridge.dart';
import 'package:vibescape/core/env/env.dart';
import 'package:vibescape/features/auth/providers.dart' as auth;
import 'package:vibescape/features/library/providers.dart' as libf;
import 'package:vibescape/features/native_audio/providers.dart' as native;
import 'package:vibescape/features/player/providers.dart' as player;
import 'package:vibescape/features/queue/providers.dart' as queue;

/// Overrides applied at the root ProviderScope in `main.dart`.
///
/// [audioHandler] comes from `AudioService.init(...)` — see main.dart.
List<Override> productionOverrides({BaseAudioHandler? audioHandler}) {
  return [
    // Native audio: real Spotify SDK bridge, wrapped by audio_service.
    native.nativePlayerProvider.overrideWith((ref) {
      final env = ref.watch(envProvider);
      return native.SpotifyNativePlayer(
        clientId: env.spotifyClientId,
        redirectUrl: env.spotifyRedirectUri,
      );
    }),

    // Auth: forward to the shared HTTP client.
    auth.authApiProvider.overrideWith(
      (ref) => VibescapeAuthApiAdapter(ref.watch(vibeScapeApiProvider)),
    ),

    // Queue: forward.
    queue.queueApiProvider.overrideWith(
      (ref) => VibescapeQueueApiAdapter(ref.watch(vibeScapeApiProvider)),
    ),

    // Library: forward. Spotify token callback plumbs the current user's
    // linked-Spotify access token from secure storage; returns null until
    // the OAuth flow completes, which surfaces as an AuthFailure at the
    // sync/library-list call sites.
    libf.libraryApiProvider.overrideWith(
      (ref) => VibescapeLibraryApiAdapter(
        ref.watch(vibeScapeApiProvider),
        () async {
          final storage =
              await ref.read(auth.authLocalStorageProvider.future);
          final authState = ref.read(auth.authControllerProvider).value;
          if (authState is! auth.Authenticated) return null;
          return storage.getSpotifyToken(authState.session.profile.userId);
        },
      ),
    ),

    // Player: two providers — its native handle mirrors the native_audio one,
    // and its VibescapeApi slice forwards to the shared client.
    player.nativePlayerProvider.overrideWith(
      (ref) => NativePlayerBridge(ref.watch(native.nativePlayerProvider)),
    ),
    player.vibescapeApiProvider.overrideWith(
      (ref) => VibescapePlayerApiAdapter(ref.watch(vibeScapeApiProvider)),
    ),
  ];
}
