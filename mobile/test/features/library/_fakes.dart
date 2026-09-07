import 'dart:async';

import 'package:vibescape/core/errors/failure.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/features/library/application/library_api.dart';
import 'package:vibescape/features/library/domain/search_result.dart';
import 'package:vibescape/features/library/domain/sync_selection.dart';

class FakeLibraryApi implements LibraryApi {
  FakeLibraryApi({
    this.searchResults = const [],
    this.playlists = const [],
    this.jobId = 'job-1',
    this.progressEvents = const [],
    this.searchError,
  });

  List<SearchResult> searchResults;
  List<SyncPlaylist> playlists;
  String jobId;
  List<SyncProgress> progressEvents;
  Failure? searchError;

  int searchCalls = 0;
  String? lastQuery;
  int startSyncCalls = 0;
  SyncSelection? lastSelection;

  @override
  Future<Result<List<SearchResult>>> searchTracks(String query) async {
    searchCalls += 1;
    lastQuery = query;
    if (searchError != null) return Result.err(searchError!);
    return Result.ok(searchResults);
  }

  @override
  Future<Result<List<SyncPlaylist>>> listPlaylists() async {
    return Result.ok(playlists);
  }

  @override
  Future<Result<String>> startSync(SyncSelection selection) async {
    startSyncCalls += 1;
    lastSelection = selection;
    return Result.ok(jobId);
  }

  @override
  Stream<SyncProgress> syncJobStream(String jobId) async* {
    for (final e in progressEvents) {
      await Future<void>.delayed(const Duration(milliseconds: 5));
      yield e;
    }
  }
}
