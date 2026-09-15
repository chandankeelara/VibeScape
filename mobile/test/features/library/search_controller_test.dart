import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/features/library/application/library_api.dart';
import 'package:vibescape/features/library/application/search_controller.dart';
import 'package:vibescape/features/library/domain/search_result.dart';

import '../../helpers/pump.dart';
import '_fakes.dart';

void main() {
  group('SearchController', () {
    test('empty query resets state without hitting the API', () async {
      final api = FakeLibraryApi();
      final c = makeContainer(overrides: [libraryApiProvider.overrideWithValue(api)]);
      final ctl = c.read(searchControllerProvider.notifier);
      ctl.setQuery('  ');
      expect(api.searchCalls, 0);
      expect(c.read(searchControllerProvider).value, isEmpty);
    });

    test('submit bypasses debounce', () async {
      final api = FakeLibraryApi(searchResults: [
        const SearchResult(
          id: 't1',
          title: 'Title',
          artist: 'A',
          origin: SearchOrigin.library,
        ),
      ]);
      final c = makeContainer(overrides: [libraryApiProvider.overrideWithValue(api)]);
      await c.read(searchControllerProvider.notifier).submit('hello');
      expect(api.searchCalls, 1);
      expect(api.lastQuery, 'hello');
      expect(c.read(searchControllerProvider).value!.length, 1);
    });

    test('debounced setQuery collapses rapid keystrokes into one call',
        () async {
      final api = FakeLibraryApi();
      final c = makeContainer(overrides: [libraryApiProvider.overrideWithValue(api)]);
      final ctl = c.read(searchControllerProvider.notifier);
      ctl.setQuery('h');
      ctl.setQuery('he');
      ctl.setQuery('hel');
      ctl.setQuery('hell');
      ctl.setQuery('hello');
      await Future<void>.delayed(const Duration(milliseconds: 350));
      expect(api.searchCalls, 1);
      expect(api.lastQuery, 'hello');
    });
  });
}
