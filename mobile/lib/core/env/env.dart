import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Two-mode runtime config: `dev` (points at a local backend) or `prod`.
///
/// All fields resolve at compile time via `--dart-define=KEY=value` flags.
/// Baked into the binary — no runtime `.env` file, no dotenv bundling.
///
///   flutter run -d chrome --dart-define=VIBESCAPE_ENV=dev
///   flutter build web       --dart-define=VIBESCAPE_ENV=prod \
///                            --dart-define=VIBESCAPE_API_BASE_URL=https://api.example.com
///
/// Empty [apiBaseUrl] (the default) means "use relative URLs" — the app
/// talks to the same origin it was served from. Correct behavior when the
/// backend serves the Flutter build.
enum AppMode { dev, prod }

class Env {
  const Env({
    required this.mode,
    required this.apiBaseUrl,
    required this.spotifyClientId,
    required this.spotifyRedirectUri,
  });

  factory Env.fromDefines() {
    const modeStr = String.fromEnvironment('VIBESCAPE_ENV', defaultValue: 'prod');
    final mode = modeStr.toLowerCase() == 'dev' ? AppMode.dev : AppMode.prod;
    return Env(
      mode: mode,
      apiBaseUrl: const String.fromEnvironment(
        'VIBESCAPE_API_BASE_URL',
        defaultValue: '',
      ),
      spotifyClientId: const String.fromEnvironment(
        'SPOTIFY_CLIENT_ID',
        defaultValue: '',
      ),
      spotifyRedirectUri: const String.fromEnvironment(
        'SPOTIFY_REDIRECT_URI',
        defaultValue: 'vibescape://spotify-auth',
      ),
    );
  }

  final AppMode mode;
  final String apiBaseUrl;
  final String spotifyClientId;
  final String spotifyRedirectUri;

  bool get isDev => mode == AppMode.dev;
  bool get isProd => mode == AppMode.prod;
}

final envProvider = Provider<Env>((ref) {
  throw UnimplementedError('envProvider must be overridden in main.dart / tests');
});
