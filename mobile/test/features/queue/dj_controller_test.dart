import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/features/queue/application/dj_controller.dart';
import 'package:vibescape/features/queue/domain/dj_state.dart';

import '../../helpers/pump.dart';

void main() {
  group('DjController — event buffer', () {
    test('recordQueued pushes an event and buildWeights returns positive', () {
      final c = makeContainer();
      c.read(djControllerProvider.notifier).recordQueued('t1');
      final w = c.read(djControllerProvider.notifier).buildWeights();
      expect(w.positives.map((e) => e.id), ['t1']);
      expect(w.negatives, isEmpty);
      expect(w.positives.first.weight, greaterThan(1.0)); // base=1.2
    });

    test('completed action → positive with base 0.8', () {
      final c = makeContainer();
      c.read(djControllerProvider.notifier).recordCompleted('t1');
      final w = c.read(djControllerProvider.notifier).buildWeights();
      expect(w.positives.first.weight, closeTo(0.8, 0.001));
    });

    test('skipped with played_ratio < 0.15 → strong negative', () {
      final c = makeContainer();
      final ctl = c.read(djControllerProvider.notifier)
        ..onTrackChanged('t1')
        ..recordSkipped('t1', durationMs: 100000000); // never-started ratio ≈ 0
      final w = ctl.buildWeights();
      expect(w.negatives.map((e) => e.id), ['t1']);
      expect(w.positives, isEmpty);
    });

    test('same id in both piles → resolves to larger', () {
      final c = makeContainer();
      final ctl = c.read(djControllerProvider.notifier)
        ..recordQueued('t1') // +1.2
        ..onTrackChanged('t1')
        ..recordSkipped('t1', durationMs: 100000000); // -0.8
      final w = ctl.buildWeights();
      expect(w.positives.map((e) => e.id), ['t1']);
      expect(w.negatives, isEmpty);
    });

    test('exponential decay — older events weigh less', () {
      final c = makeContainer();
      final ctl = c.read(djControllerProvider.notifier);
      // 5 completed events for different tracks. Newest event (index 4)
      // gets full weight (decay^0), oldest (index 0) gets decay^4.
      for (var i = 0; i < 5; i++) {
        ctl.recordCompleted('t$i');
      }
      final w = ctl.buildWeights();
      final byId = {for (final e in w.positives) e.id: e.weight};
      expect(byId['t4']! > byId['t0']!, isTrue);
    });

    test('excludeIds returns recent completed/next in reverse order', () {
      final c = makeContainer();
      final ctl = c.read(djControllerProvider.notifier)
        ..recordCompleted('a')
        ..recordCompleted('b')
        ..recordCompleted('c');
      expect(ctl.excludeIds(limit: 10), ['c', 'b', 'a']);
    });
  });

  group('DjController — toggle', () {
    test('toggle flips enabled and clears seed', () {
      final c = makeContainer();
      final ctl = c.read(djControllerProvider.notifier)
        ..markSeed('x')
        ..toggle();
      expect(c.read(djControllerProvider).enabled, isTrue);
      // markSeed persists across toggle-on
      expect(c.read(djControllerProvider).lastSeedId, 'x');
      ctl.toggle();
      expect(c.read(djControllerProvider).enabled, isFalse);
      expect(c.read(djControllerProvider).lastSeedId, isNull);
    });
  });

  group('DjEvent', () {
    test('json round-trips', () {
      const e = DjEvent(
        trackId: 't1',
        action: 'completed',
        playedRatio: 0.75,
        ts: 12345,
      );
      final j = e.toJson();
      final back = DjEvent.fromJson(j);
      expect(back.trackId, e.trackId);
      expect(back.action, e.action);
      expect(back.playedRatio, e.playedRatio);
      expect(back.ts, e.ts);
    });
  });
}
