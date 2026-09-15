import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/features/library/application/library_api.dart';
import 'package:vibescape/features/library/application/sync_controller.dart';
import 'package:vibescape/features/library/domain/sync_selection.dart';
import 'package:vibescape/features/library/presentation/screens/sync_modal.dart';
import 'package:vibescape/features/library/presentation/widgets/sync_progress_view.dart';

import '../../helpers/pump.dart';
import '_fakes.dart';

void main() {
  testWidgets('shows both tabs and switches between them', (tester) async {
    final api = FakeLibraryApi(playlists: const [
      SyncPlaylist(id: 'p1', name: 'Playlist One', trackCount: 3),
    ]);
    await pumpWithProviders(
      tester,
      const SizedBox(width: 400, height: 800, child: SyncModal()),
      overrides: [libraryApiProvider.overrideWithValue(api)],
    );
    await tester.pumpAndSettle();
    expect(find.text('Sync my library'), findsOneWidget);
    expect(find.text('Add public playlist'), findsOneWidget);
    expect(find.text('Playlist One'), findsOneWidget);

    await tester.tap(find.text('Add public playlist'));
    await tester.pumpAndSettle();
    expect(find.text('Paste a public Spotify playlist URL to import its tracks.'),
        findsOneWidget);
  });

  testWidgets('progress view tweens the bar and renders counts',
      (tester) async {
    await pumpWithProviders(
      tester,
      const SyncProgressView(
        progress: SyncProgress(
          done: 3,
          total: 10,
          added: 2,
          alreadyYours: 1,
          queued: 0,
          currentTrack: 'A track',
        ),
      ),
    );
    // Initial frame — tween starts at 0.
    await tester.pump();
    // Halfway through the tween.
    await tester.pump(const Duration(milliseconds: 140));
    // Settle to final value.
    await tester.pumpAndSettle();
    expect(find.text('30%'), findsOneWidget);
    expect(find.text('3 / 10'), findsOneWidget);
    expect(find.text('A track'), findsOneWidget);
    expect(find.text('added'), findsOneWidget);
    expect(find.text('already yours'), findsOneWidget);
    expect(find.text('queued'), findsOneWidget);
  });
}
