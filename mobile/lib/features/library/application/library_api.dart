import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/features/library/domain/search_result.dart';
import 'package:vibescape/features/library/domain/sync_selection.dart';

/// API surface the library feature depends on. Agent A owns the concrete
/// impl in `lib/core/api/` and re-exports it as `vibescapeApiProvider`.
///
/// TODO(agent-a): once the API layer lands, override this provider at
/// bootstrap to forward calls:
///
///     libraryApiProvider.overrideWith((ref) {
///       final api = ref.watch(vibescapeApiProvider);
///       return _LibraryApiAdapter(api);
///     })
abstract class LibraryApi {
  /// Search across local library + Spotify catalog. Backend endpoint:
  /// `GET /api/search?q=...`. Empty query returns an empty list.
  Future<Result<List<SearchResult>>> searchTracks(String query);

  /// List the user's Spotify playlists (owned + followed). Feeds the
  /// checkbox list in the "Sync my library" tab.
  Future<Result<List<SyncPlaylist>>> listPlaylists();

  /// Kick off a sync job. Returns the job id so the caller can subscribe
  /// to its progress stream.
  Future<Result<String>> startSync(SyncSelection selection);

  /// Live progress updates for a running sync job. Stream closes when the
  /// job finishes (final event carries `finished: true`).
  Stream<SyncProgress> syncJobStream(String jobId);
}

final libraryApiProvider = Provider<LibraryApi>((ref) {
  throw UnimplementedError(
    'libraryApiProvider must be overridden. Agent A: forward to '
    'vibescapeApiProvider once library methods exist.',
  );
});
