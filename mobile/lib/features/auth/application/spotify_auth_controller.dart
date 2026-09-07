import 'dart:async';

import 'package:app_links/app_links.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:vibescape/features/auth/application/auth_api.dart';
import 'package:vibescape/features/auth/application/auth_controller.dart';

/// Launches the Spotify authorize URL and captures the `?code=` callback.
///
/// On mobile the callback arrives as a `vibescape://spotify-auth?code=...`
/// deep link routed by `app_links`.
/// On web the callback arrives as a query param on the current page URL
/// (Spotify redirects to `<origin>/spotify-callback?code=...` per the
/// registered redirect_uri).
class SpotifyAuthController {
  SpotifyAuthController(this.ref);
  final Ref ref;

  StreamSubscription<Uri>? _linkSub;

  Future<void> start() async {
    final api = ref.read(authApiProvider);
    final cfg = await api.spotifyConfig();
    await cfg.when(
      ok: (c) async => _launch(c),
      err: (_) async {},
    );
  }

  Future<void> _launch(SpotifyOAuthConfig cfg) async {
    final scopes = [
      'streaming',
      'user-read-email',
      'user-read-private',
      'user-library-read',
      'user-top-read',
      'playlist-read-private',
    ].join(' ');
    final url = Uri.https('accounts.spotify.com', '/authorize', {
      'client_id': cfg.clientId,
      'response_type': 'code',
      'redirect_uri': cfg.redirectUri,
      'scope': scopes,
    });

    if (kIsWeb) {
      // Web: full-page redirect — the callback lands back on us with ?code=.
      await launchUrl(url, webOnlyWindowName: '_self');
      return;
    }

    // Mobile: open the system browser, listen for the deep-link callback.
    await _listenForDeepLink();
    await launchUrl(url, mode: LaunchMode.externalApplication);
  }

  Future<void> _listenForDeepLink() async {
    await _linkSub?.cancel();
    final links = AppLinks();
    _linkSub = links.uriLinkStream.listen((uri) {
      final code = uri.queryParameters['code'];
      if (code != null && code.isNotEmpty) {
        ref.read(authControllerProvider.notifier).submitSpotifyCode(code);
        _linkSub?.cancel();
      }
    });
  }

  /// Called from `main.dart` on web: on cold-start, if the current URL has
  /// a Spotify auth code, feed it to the auth controller.
  ///
  /// Accepts both `?code=…` (direct-redirect flow) and `?spotify_code=…`
  /// (backend `/callback` HTML → `/?spotify_code=…` redirect flow, matching
  /// `frontend/login.js`).
  Future<void> consumeWebCallbackIfPresent() async {
    if (!kIsWeb) return;
    final uri = Uri.base;
    final code = uri.queryParameters['spotify_code'] ??
        uri.queryParameters['code'];
    if (code == null || code.isEmpty) return;
    await ref.read(authControllerProvider.notifier).submitSpotifyCode(code);
  }

  void dispose() {
    _linkSub?.cancel();
  }
}

final spotifyAuthControllerProvider =
    Provider<SpotifyAuthController>((ref) {
  final c = SpotifyAuthController(ref);
  ref.onDispose(c.dispose);
  return c;
});
