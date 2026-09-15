import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/features/library/application/library_api.dart';
import 'package:vibescape/features/library/domain/search_result.dart';
import 'package:vibescape/features/library/presentation/widgets/search_bar.dart';

import '../../helpers/pump.dart';
import '_fakes.dart';

void main() {
  testWidgets('renders text field with hint', (tester) async {
    await pumpWithProviders(
      tester,
      const LibrarySearchBar(),
      overrides: [libraryApiProvider.overrideWithValue(FakeLibraryApi())],
    );
    expect(find.text('Search your library and Spotify…'), findsOneWidget);
  });

  testWidgets('debounced typing eventually shows results', (tester) async {
    final api = FakeLibraryApi(searchResults: const [
      SearchResult(
        id: 'z',
        title: 'Zephyr Song',
        artist: 'RHCP',
        origin: SearchOrigin.library,
      ),
    ]);
    await pumpWithProviders(
      tester,
      const LibrarySearchBar(),
      overrides: [libraryApiProvider.overrideWithValue(api)],
    );
    await tester.enterText(find.byType(TextField), 'ze');
    await tester.pump(); // focus
    // Wait past the 250ms debounce.
    await tester.pump(const Duration(milliseconds: 300));
    // Let the async future resolve.
    await tester.pumpAndSettle();
    expect(find.text('Zephyr Song'), findsOneWidget);
    expect(api.searchCalls, 1);
  });
}
