import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/features/queue/application/queue_controller.dart';
import 'package:vibescape/features/queue/domain/queue_track.dart';

import '../../helpers/pump.dart';

QueueTrack _t(String id) => QueueTrack(id: id, title: 't-$id', artist: 'a');

void main() {
  group('QueueController', () {
    test('starts empty', () {
      final c = makeContainer();
      expect(c.read(queueControllerProvider).isEmpty, true);
    });

    test('add appends and dedupes trailing duplicates', () {
      final c = makeContainer();
      final ctl = c.read(queueControllerProvider.notifier);
      ctl.add(_t('1'));
      ctl.add(_t('2'));
      ctl.add(_t('2')); // dedup trailing
      expect(c.read(queueControllerProvider).tracks.map((t) => t.id), ['1', '2']);
    });

    test('insertAt clamps index', () {
      final c = makeContainer();
      final ctl = c.read(queueControllerProvider.notifier);
      ctl.add(_t('a'));
      ctl.insertAt(999, _t('b'));
      ctl.insertAt(-5, _t('z'));
      expect(c.read(queueControllerProvider).tracks.map((t) => t.id),
          ['z', 'a', 'b']);
    });

    test('removeAt removes; out-of-range is a no-op', () {
      final c = makeContainer();
      final ctl = c.read(queueControllerProvider.notifier);
      ctl.add(_t('a'));
      ctl.add(_t('b'));
      ctl.removeAt(99);
      ctl.removeAt(0);
      expect(c.read(queueControllerProvider).tracks.map((t) => t.id), ['b']);
    });

    test('reorder moves items with Flutter-style index math', () {
      final c = makeContainer();
      final ctl = c.read(queueControllerProvider.notifier);
      ctl.add(_t('a'));
      ctl.add(_t('b'));
      ctl.add(_t('c'));
      // Move index 0 to position 2 (Flutter passes newIndex = 3)
      ctl.reorder(0, 3);
      expect(c.read(queueControllerProvider).tracks.map((t) => t.id),
          ['b', 'c', 'a']);
    });

    test('advanceNext pops head and sets currentId', () {
      final c = makeContainer();
      final ctl = c.read(queueControllerProvider.notifier);
      ctl.add(_t('a'));
      ctl.add(_t('b'));
      final next = ctl.advanceNext();
      expect(next?.id, 'a');
      final s = c.read(queueControllerProvider);
      expect(s.currentId, 'a');
      expect(s.tracks.map((t) => t.id), ['b']);
    });

    test('jumpTo skips tracks in between', () {
      final c = makeContainer();
      final ctl = c.read(queueControllerProvider.notifier);
      ctl.add(_t('a'));
      ctl.add(_t('b'));
      ctl.add(_t('c'));
      final hit = ctl.jumpTo(1);
      expect(hit?.id, 'b');
      final s = c.read(queueControllerProvider);
      expect(s.currentId, 'b');
      expect(s.tracks.map((t) => t.id), ['c']);
    });

    test('clear wipes everything', () {
      final c = makeContainer();
      final ctl = c.read(queueControllerProvider.notifier);
      ctl.add(_t('a'));
      ctl.setCurrent('a');
      ctl.clear();
      final s = c.read(queueControllerProvider);
      expect(s.tracks, isEmpty);
      expect(s.currentId, isNull);
    });
  });
}
