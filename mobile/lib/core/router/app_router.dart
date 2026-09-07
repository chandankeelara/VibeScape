import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:vibescape/features/auth/providers.dart';
import 'package:vibescape/features/player/presentation/screens/now_playing_screen.dart';

/// Central router.
///
/// Two routes: `/` (landing) when signed out, `/player` when authenticated.
/// A redirect keeps state in sync — screens never navigate manually, they
/// just call auth controller intents and the router follows.
final routerProvider = Provider<GoRouter>((ref) {
  final auth = ref.watch(authControllerProvider);

  return GoRouter(
    initialLocation: '/',
    debugLogDiagnostics: false,
    redirect: (context, state) {
      final signedIn = auth.value is Authenticated;
      final atLanding = state.matchedLocation == '/';
      if (signedIn && atLanding) return '/player';
      if (!signedIn && !atLanding) return '/';
      return null;
    },
    refreshListenable: _RiverpodListenable(ref),
    routes: [
      GoRoute(
        path: '/',
        name: 'landing',
        builder: (_, __) => const LandingScreen(),
      ),
      GoRoute(
        path: '/player',
        name: 'now-playing',
        builder: (_, __) => const NowPlayingScreen(),
      ),
    ],
    errorBuilder: (_, state) => Scaffold(
      body: Center(child: Text('Route error: ${state.error}')),
    ),
  );
});

/// Bridge: `go_router` wants a `Listenable` for refresh triggers; Riverpod
/// speaks Providers. This wraps `authControllerProvider` so the router
/// re-evaluates redirects whenever auth state changes.
class _RiverpodListenable extends ChangeNotifier {
  _RiverpodListenable(Ref ref) {
    ref.listen(authControllerProvider, (_, __) => notifyListeners());
  }
}
