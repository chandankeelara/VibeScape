import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/errors/failure.dart';
import 'package:vibescape/features/library/application/library_api.dart';
import 'package:vibescape/features/library/domain/search_result.dart';

const Duration _searchDebounce = Duration(milliseconds: 250);

/// Debounced async search. Widgets call [setQuery] on every keystroke; the
/// controller waits [_searchDebounce] before firing to avoid one-req-per-key.
class SearchController extends AsyncNotifier<List<SearchResult>> {
  Timer? _debounce;
  int _seq = 0;
  String _lastQuery = '';

  @override
  FutureOr<List<SearchResult>> build() {
    ref.onDispose(() => _debounce?.cancel());
    return const [];
  }

  /// User typed. Empty query resets state immediately (no debounce).
  void setQuery(String q) {
    final trimmed = q.trim();
    _lastQuery = trimmed;
    _debounce?.cancel();
    if (trimmed.isEmpty) {
      state = const AsyncData([]);
      return;
    }
    _debounce = Timer(_searchDebounce, () => _fire(trimmed));
  }

  /// Bypass the debounce (e.g. user pressed Enter).
  Future<void> submit(String q) async {
    final trimmed = q.trim();
    _debounce?.cancel();
    if (trimmed.isEmpty) {
      state = const AsyncData([]);
      return;
    }
    _lastQuery = trimmed;
    await _fire(trimmed);
  }

  void clear() {
    _debounce?.cancel();
    _lastQuery = '';
    state = const AsyncData([]);
  }

  Future<void> _fire(String query) async {
    final api = ref.read(libraryApiProvider);
    final mySeq = ++_seq;
    state = const AsyncLoading<List<SearchResult>>().copyWithPrevious(state);

    final result = await api.searchTracks(query);
    if (mySeq != _seq || query != _lastQuery) return;

    result.when(
      ok: (list) => state = AsyncData(list),
      err: (Failure f) =>
          state = AsyncError<List<SearchResult>>(f, StackTrace.current),
    );
  }
}

final searchControllerProvider =
    AsyncNotifierProvider<SearchController, List<SearchResult>>(
  SearchController.new,
);
