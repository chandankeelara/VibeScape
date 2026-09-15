import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/features/player/providers.dart';

import '../../../helpers/pump.dart';
import '../_fakes.dart';

void main() {
  group('PlayerController', () {
    late FakeNativePlayer player;
    late FakeVibescapeApi api;

    setUp(() {
      player = FakeNativePlayer();
      api = FakeVibescapeApi();
    });

    tearDown(() async {
      await player.dispose();
    });

    test('loadAndPlay pushes uri to native player and updates state',
        () async {
      final c = makeContainer(overrides: [
        nativePlayerProvider.overrideWithValue(player),
        vibescapeApiProvider.overrideWithValue(api),
      ]);
      // Ensure controller subscribes.
      await c.read(playerControllerProvider.future);

      final t = sampleTrack();
      await c.read(playerControllerProvider.notifier).loadAndPlay(t);

      expect(player.playCalls, contains(t.previewUrl));
      final state = c.read(playerControllerProvider).value!;
      expect(state.track?.id, t.id);
    });

    test('togglePlayPause calls pause when playing, resume when paused',
        () async {
      final c = makeContainer(overrides: [
        nativePlayerProvider.overrideWithValue(player),
        vibescapeApiProvider.overrideWithValue(api),
      ]);
      await c.read(playerControllerProvider.future);
      await c.read(playerControllerProvider.notifier)
          .loadAndPlay(sampleTrack());

      // native emits playing=true.
      player.emit(player.currentState.copyWith(playing: true, durationMs: 30000));
      await Future<void>.delayed(Duration.zero);
      await c.read(playerControllerProvider.notifier).togglePlayPause();
      expect(player.pauseCalls, 1);

      player.emit(player.currentState.copyWith(playing: false));
      await Future<void>.delayed(Duration.zero);
      await c.read(playerControllerProvider.notifier).togglePlayPause();
      expect(player.resumeCalls, 1);
    });

    test('next() with no history calls api and loads returned track',
        () async {
      final rec = sampleTrack(id: 'rec');
      api.nextResult = Result<PlayerTrack>.ok(rec);
      final c = makeContainer(overrides: [
        nativePlayerProvider.overrideWithValue(player),
        vibescapeApiProvider.overrideWithValue(api),
      ]);
      await c.read(playerControllerProvider.future);
      await c.read(playerControllerProvider.notifier).next(vibe: 42);

      expect(api.nextCalls, [42]);
      expect(player.playCalls.last, rec.previewUrl);
    });

    test('previous seeks to 0 when history has one entry', () async {
      final c = makeContainer(overrides: [
        nativePlayerProvider.overrideWithValue(player),
        vibescapeApiProvider.overrideWithValue(api),
      ]);
      await c.read(playerControllerProvider.future);
      await c.read(playerControllerProvider.notifier)
          .loadAndPlay(sampleTrack());
      await c.read(playerControllerProvider.notifier).previous();
      expect(player.seekCalls, [0]);
    });

    test('scrubbing decouples position updates from native stream', () async {
      final c = makeContainer(overrides: [
        nativePlayerProvider.overrideWithValue(player),
        vibescapeApiProvider.overrideWithValue(api),
      ]);
      await c.read(playerControllerProvider.future);
      await c.read(playerControllerProvider.notifier)
          .loadAndPlay(sampleTrack());

      c.read(playerControllerProvider.notifier).beginScrub();
      c.read(playerControllerProvider.notifier).updateScrubPosition(12000);

      // Native pushes a stale position — should be ignored while scrubbing.
      player.emit(player.currentState.copyWith(positionMs: 500, durationMs: 30000));
      await Future<void>.delayed(Duration.zero);
      expect(
        c.read(playerControllerProvider).value!.positionMs,
        12000,
      );

      await c.read(playerControllerProvider.notifier).endScrub(15000);
      expect(player.seekCalls.last, 15000);
    });
  });
}
