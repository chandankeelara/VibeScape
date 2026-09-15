import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/core/env/env.dart';
import 'package:vibescape/core/theme/app_theme.dart';

/// Boots a widget wrapped in MaterialApp + ProviderScope for tests.
///
/// Callers pass `overrides` to inject mocked providers (mock api client,
/// fake native player, etc.). The env provider is pre-populated so widgets
/// that indirectly read it don't blow up.
Future<void> pumpWithProviders(
  WidgetTester tester,
  Widget widget, {
  List<Override> overrides = const [],
}) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        envProvider.overrideWithValue(
          const Env(mode: AppMode.dev,
            apiBaseUrl: 'http://test.local',
            spotifyClientId: 'test_client',
            spotifyRedirectUri: 'vibescape://spotify-auth',
          ),
        ),
        ...overrides,
      ],
      child: MaterialApp(
        theme: AppTheme.dark,
        home: Scaffold(body: widget),
      ),
    ),
  );
}

/// Build a fresh ProviderContainer for unit tests. Always `addTearDown` the
/// dispose so tests don't leak state between runs.
ProviderContainer makeContainer({List<Override> overrides = const []}) {
  final container = ProviderContainer(
    overrides: [
      envProvider.overrideWithValue(
        const Env(mode: AppMode.dev,
          apiBaseUrl: 'http://test.local',
          spotifyClientId: 'test_client',
          spotifyRedirectUri: 'vibescape://spotify-auth',
        ),
      ),
      ...overrides,
    ],
  );
  addTearDown(container.dispose);
  return container;
}
