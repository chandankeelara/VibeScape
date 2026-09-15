import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/features/library/application/library_api.dart';
import 'package:vibescape/features/library/application/sync_controller.dart';
import 'package:vibescape/features/library/domain/sync_selection.dart';

import '../../helpers/pump.dart';
import '_fakes.dart';

void main() {
  group('SyncController', () {
    test('loadPlaylists moves phase to awaitingSelection', () async {
      final api = FakeLibraryApi(playlists: const [
        SyncPlaylist(id: 'p1', name: 'Chill', trackCount: 12),
      ]);
      final c = makeContainer(overrides: [libraryApiProvider.overrideWithValue(api)]);
      await c.read(syncControllerProvider.notifier).loadPlaylists();
      final s = c.read(syncControllerProvider);
      expect(s.phase, SyncPhase.awaitingSelection);
      expect(s.playlists.first.name, 'Chill');
    });

    test('togglePlaylist adds and removes ids', () {
      final c = makeContainer(overrides: [libraryApiProvider.overrideWithValue(FakeLibraryApi())]);
      final ctl = c.read(syncControllerProvider.notifier);
      ctl.togglePlaylist('a', true);
      ctl.togglePlaylist('b', true);
      ctl.togglePlaylist('a', false);
      expect(c.read(syncControllerProvider).selection.playlistIds, {'b'});
    });

    test('startSync no-ops when selection is empty', () async {
      final api = FakeLibraryApi();
      final c = makeContainer(overrides: [libraryApiProvider.overrideWithValue(api)]);
      await c.read(syncControllerProvider.notifier).startSync();
      expect(api.startSyncCalls, 0);
    });

    test('startSync subscribes and completes when stream signals finished',
        () async {
      final api = FakeLibraryApi(
        progressEvents: const [
          SyncProgress(done: 1, total: 2, added: 1, alreadyYours: 0, queued: 0),
          SyncProgress(
            done: 2,
            total: 2,
            added: 1,
            alreadyYours: 1,
            queued: 0,
            finished: true,
          ),
        ],
      );
      final c = makeContainer(overrides: [libraryApiProvider.overrideWithValue(api)]);
      final ctl = c.read(syncControllerProvider.notifier);
      ctl.setIncludeLiked(true);
      await ctl.startSync();
      // Wait for the fake stream to drain.
      await Future<void>.delayed(const Duration(milliseconds: 60));
      final s = c.read(syncControllerProvider);
      expect(s.phase, SyncPhase.complete);
      expect(s.progress?.done, 2);
      expect(api.startSyncCalls, 1);
    });
  });
}
