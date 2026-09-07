import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/features/player/providers.dart';

import '../../../helpers/pump.dart';
import '../_fakes.dart';

void main() {
  group('VibeController', () {
    test('setVibe clamps and updates state', () {
      final c = makeContainer();
      final ctrl = c.read(vibeControllerProvider.notifier);
      ctrl.setVibe(150);
      expect(c.read(vibeControllerProvider), 100);
      ctrl.setVibe(-5);
      expect(c.read(vibeControllerProvider), 0);
      ctrl.setVibe(42);
      expect(c.read(vibeControllerProvider), 42);
    });

    test('crossedThreshold fires only when mood bucket changes', () {
      final c = makeContainer();
      final ctrl = c.read(vibeControllerProvider.notifier);
      // Default state is 50 → 'steady'.
      expect(ctrl.crossedThreshold(55), isFalse); // still steady
      expect(ctrl.crossedThreshold(80), isTrue); // beast
      expect(ctrl.crossedThreshold(85), isFalse); // still beast
      expect(ctrl.crossedThreshold(15), isTrue); // sleep
    });

    test('commit debounces then calls player next() with current vibe',
        () async {
      final player = FakeNativePlayer();
      addTearDown(player.dispose);
      final api = FakeVibescapeApi(
        nextResult: Result<PlayerTrack>.ok(sampleTrack(id: 'rec')),
      );
      final c = makeContainer(overrides: [
        nativePlayerProvider.overrideWithValue(player),
        vibescapeApiProvider.overrideWithValue(api),
      ]);
      await c.read(playerControllerProvider.future);
      c.read(vibeControllerProvider.notifier).setVibe(77);
      c.read(vibeControllerProvider.notifier)
          .commit(debounce: const Duration(milliseconds: 20));

      // Not yet fired.
      expect(api.nextCalls, isEmpty);
      await Future<void>.delayed(const Duration(milliseconds: 60));
      expect(api.nextCalls, [77]);
    });

    test('rapid commit cancels earlier pending fetch', () async {
      final player = FakeNativePlayer();
      addTearDown(player.dispose);
      final api = FakeVibescapeApi(
        nextResult: Result<PlayerTrack>.ok(sampleTrack(id: 'rec')),
      );
      final c = makeContainer(overrides: [
        nativePlayerProvider.overrideWithValue(player),
        vibescapeApiProvider.overrideWithValue(api),
      ]);
      await c.read(playerControllerProvider.future);
      final vc = c.read(vibeControllerProvider.notifier);
      vc.setVibe(20);
      vc.commit(debounce: const Duration(milliseconds: 40));
      await Future<void>.delayed(const Duration(milliseconds: 10));
      vc.setVibe(90);
      vc.commit(debounce: const Duration(milliseconds: 40));
      await Future<void>.delayed(const Duration(milliseconds: 80));
      expect(api.nextCalls, [90]);
    });
  });
}
