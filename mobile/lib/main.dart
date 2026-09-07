import 'package:audio_service/audio_service.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/app.dart';
import 'package:vibescape/core/bootstrap/bootstrap.dart';
import 'package:vibescape/core/env/env.dart';
import 'package:vibescape/features/auth/providers.dart';
import 'package:vibescape/features/native_audio/providers.dart' as native;

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await dotenv.load(fileName: '.env');
  final env = Env.fromDotenv();

  // audio_service uses native platform channels — skip on web where it
  // isn't implemented (falls back to no-op handler via native_audio's fake).
  final audioHandler = kIsWeb
      ? null
      : await AudioService.init(
          builder: native.VibescapeAudioHandler.new,
          config: const AudioServiceConfig(
            androidNotificationChannelId: 'com.vibescape.app.audio',
            androidNotificationChannelName: 'VibeScape playback',
            androidNotificationOngoing: true,
            androidStopForegroundOnPause: true,
          ),
        );

  runApp(
    ProviderScope(
      overrides: [
        envProvider.overrideWithValue(env),
        ...productionOverrides(audioHandler: audioHandler),
      ],
      child: Consumer(
        builder: (context, ref, child) {
          // On web, capture ?code=... on cold-start and complete Spotify auth.
          if (kIsWeb) {
            WidgetsBinding.instance.addPostFrameCallback((_) {
              ref
                  .read(spotifyAuthControllerProvider)
                  .consumeWebCallbackIfPresent();
            });
          }
          return const VibeScapeApp();
        },
      ),
    ),
  );
}
