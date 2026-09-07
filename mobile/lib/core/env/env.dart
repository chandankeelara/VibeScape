import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Two-mode runtime config: `dev` (points at your local backend on
/// 127.0.0.1) or `prod` (points at the fly.io deploy).
///
/// Resolution order for every field:
///   1. `.env` file overrides (dotenv) — takes precedence, so power users
///      can point either mode at a custom URL.
///   2. Per-mode built-in default (see [_defaults]).
///
/// Mode is selected via `--dart-define=VIBESCAPE_ENV=dev` at build/run time;
/// defaults to `prod`. Convenience commands:
///
///   flutter run -d chrome --dart-define=VIBESCAPE_ENV=dev
///   flutter build web       --dart-define=VIBESCAPE_ENV=prod
enum AppMode { dev, prod }

class Env {
  const Env({
    required this.mode,
    required this.apiBaseUrl,
    required this.spotifyClientId,
    required this.spotifyRedirectUri,
  });

  factory Env.fromDotenv() {
    final mode = _resolveMode();
    final d = _defaults[mode]!;
    return Env(
      mode: mode,
      apiBaseUrl: _dot('VIBESCAPE_API_BASE_URL') ?? d.apiBaseUrl,
      spotifyClientId: _dot('SPOTIFY_CLIENT_ID') ?? d.spotifyClientId,
      spotifyRedirectUri: _dot('SPOTIFY_REDIRECT_URI') ?? d.spotifyRedirectUri,
    );
  }

  final AppMode mode;
  final String apiBaseUrl;
  final String spotifyClientId;
  final String spotifyRedirectUri;

  bool get isDev => mode == AppMode.dev;
  bool get isProd => mode == AppMode.prod;

  static AppMode _resolveMode() {
    const compileFlag =
        String.fromEnvironment('VIBESCAPE_ENV', defaultValue: '');
    final dotenvFlag = _dot('VIBESCAPE_ENV');
    final raw = compileFlag.isNotEmpty ? compileFlag : (dotenvFlag ?? 'prod');
    return raw.toLowerCase() == 'dev' ? AppMode.dev : AppMode.prod;
  }

  static String? _dot(String key) {
    // dotenv throws if load() hasn't run; guard by checking `isInitialized`.
    if (!dotenv.isInitialized) return null;
    final v = dotenv.env[key];
    return (v == null || v.isEmpty) ? null : v;
  }
}

class _Defaults {
  const _Defaults({
    required this.apiBaseUrl,
    required this.spotifyClientId,
    required this.spotifyRedirectUri,
  });
  final String apiBaseUrl;
  final String spotifyClientId;
  final String spotifyRedirectUri;
}

/// Empty [apiBaseUrl] means "use relative URLs" — the Flutter build is
/// being served from the same origin as the backend (recommended for
/// Spotify OAuth on web, since the `/callback` redirect lands back on the
/// same origin the Flutter app is running from).
const _defaults = <AppMode, _Defaults>{
  AppMode.dev: _Defaults(
    apiBaseUrl: '',
    spotifyClientId: '',
    spotifyRedirectUri: 'vibescape://spotify-auth',
  ),
  AppMode.prod: _Defaults(
    apiBaseUrl: '',
    spotifyClientId: '',
    spotifyRedirectUri: 'vibescape://spotify-auth',
  ),
};

final envProvider = Provider<Env>((ref) {
  throw UnimplementedError('envProvider must be overridden in main.dart / tests');
});
