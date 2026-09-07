/// Adapters bridging each feature's narrow API interface to the shared
/// `VibeScapeApi`. Kept in `core/bootstrap/` because features must not
/// import each other or reach into `core/api/`.
library;

import 'package:vibescape/core/api/vibescape_api.dart';
import 'package:vibescape/core/errors/failure.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/core/models/session_user.dart' as api;
import 'package:vibescape/core/models/similar_tracks.dart' as api;
import 'package:vibescape/core/models/spotify_library.dart' as api;
import 'package:vibescape/core/models/spotify_search.dart' as api;
import 'package:vibescape/core/models/sync_job.dart' as api;
import 'package:vibescape/core/models/track.dart' as api;
import 'package:vibescape/features/auth/providers.dart';
import 'package:vibescape/features/library/providers.dart';
import 'package:vibescape/features/player/providers.dart' as player;
import 'package:vibescape/features/queue/providers.dart';

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

/// Forwards the mobile [AuthApi] to the backend `/api/auth/*` endpoints
/// via the shared [VibeScapeApi]. Matches the flow in `frontend/login.js`:
/// email login/signup, one-tap guest, and Spotify OAuth code exchange.
class VibescapeAuthApiAdapter implements AuthApi {
  VibescapeAuthApiAdapter(this._api);
  final VibeScapeApi _api;

  @override
  Future<Result<Session>> emailLogin({
    required String email,
    required String password,
  }) async {
    final res = await _api.login(email: email, password: password);
    return res.map(_authToSession);
  }

  @override
  Future<Result<Session>> emailSignup({
    required String email,
    required String password,
  }) async {
    final res = await _api.signup(email: email, password: password);
    return res.map(_authToSession);
  }

  @override
  Future<Result<Session>> guest() async {
    final res = await _api.guest();
    return res.map(_authToSession);
  }

  @override
  Future<Result<SpotifyAuthOutcome>> spotifyOauth({required String code}) async {
    final res = await _api.spotifyOauth(code: code);
    return res.map(
      (a) => SpotifyAuthOutcome(
        session: _authToSession(a),
        accessToken: a.spotifyAccessToken,
        refreshToken: a.spotifyRefreshToken,
        expiresIn: a.spotifyExpiresIn,
      ),
    );
  }

  @override
  Future<Result<SpotifyOAuthConfig>> spotifyConfig() async {
    final res = await _api.spotifyConfig();
    return res.map(
      (c) => SpotifyOAuthConfig(clientId: c.clientId, redirectUri: c.redirectUri),
    );
  }

  @override
  Future<Result<Session>> hydrate(String bearerToken) async {
    final res = await _api.me();
    return res.map(
      (me) => Session(
        token: bearerToken,
        profile: Profile(
          userId: me.userId.toString(),
          displayName: me.displayName,
          spotifyConnected: me.spotifyConnected,
          isAdmin: me.isAdmin,
          avatarUrl: me.avatarUrl,
        ),
      ),
    );
  }

  @override
  Future<Result<void>> logout() => _api.logout();

  Session _authToSession(api.AuthResponse a) => Session(
        token: a.sessionToken,
        profile: Profile(
          userId: a.userId.toString(),
          displayName: a.displayName,
          spotifyConnected: a.spotifyConnected,
          isAdmin: a.isAdmin,
          avatarUrl: a.avatarUrl,
        ),
      );
}

// ---------------------------------------------------------------------------
// Queue / DJ (recommendations)
// ---------------------------------------------------------------------------

class VibescapeQueueApiAdapter implements QueueApi {
  VibescapeQueueApiAdapter(this._api);
  final VibeScapeApi _api;

  @override
  Future<Result<List<QueueTrack>>> fetchRecommendations(
    String trackId, {
    String mode = 'vibe',
    List<({String id, double weight})> positives = const [],
    List<({String id, double weight})> negatives = const [],
    List<String> excludeIds = const [],
    int limit = 20,
  }) async {
    final res = await _api.similarTracksPost(
      trackKey: trackId,
      mode: mode,
      positiveIds: positives
          .map((e) => api.WeightedTrackId(id: e.id, weight: e.weight))
          .toList(growable: false),
      negativeIds: negatives
          .map((e) => api.WeightedTrackId(id: e.id, weight: e.weight))
          .toList(growable: false),
      excludeIds: excludeIds,
      limit: limit,
    );
    return res.map(
      (r) => r.tracks.map(_trackToQueue).toList(growable: false),
    );
  }
}

// ---------------------------------------------------------------------------
// Library (search + sync)
// ---------------------------------------------------------------------------

class VibescapeLibraryApiAdapter implements LibraryApi {
  VibescapeLibraryApiAdapter(this._api, this._spotifyToken);
  final VibeScapeApi _api;

  /// Async getter for the caller's current Spotify access token. Returns
  /// null before the user completes the Spotify OAuth link; endpoints that
  /// require it will surface an auth failure in that state.
  final Future<String?> Function() _spotifyToken;

  @override
  Future<Result<List<SearchResult>>> searchTracks(String query) async {
    if (query.trim().isEmpty) return const Result.ok([]);

    final localRes = await _api.searchTracks(query: query);
    final token = await _spotifyToken();
    final spotifyRes = token == null
        ? const Result<List<api.SpotifySearchResult>>.ok(<api.SpotifySearchResult>[])
        : await _api.spotifySearch(query: query, spotifyAccessToken: token);

    return localRes.when(
      err: Result<List<SearchResult>>.err,
      ok: (localTracks) {
        final localIds = localTracks
            .map((t) => t.spotifyId)
            .whereType<String>()
            .toSet();

        final merged = <SearchResult>[
          ...localTracks.map(_trackToSearchResult),
          ...?spotifyRes.valueOrNull?.map(
            (s) => SearchResult(
              id: s.spotifyId,
              title: s.title,
              artist: s.artist,
              origin: SearchOrigin.spotify,
              artUrl: s.artworkUrl,
              durationMs: s.durationMs,
              spotifyUri: 'spotify:track:${s.spotifyId}',
              alreadyInLibrary: localIds.contains(s.spotifyId),
            ),
          ),
        ];
        return Result.ok(merged);
      },
    );
  }

  @override
  Future<Result<List<SyncPlaylist>>> listPlaylists() async {
    final token = await _spotifyToken();
    if (token == null) {
      return const Result.err(
        Failure.auth(message: 'Link Spotify to see your library'),
      );
    }
    final res = await _api.spotifyLibrary(spotifyAccessToken: token);
    return res.map(_flattenLibrary);
  }

  @override
  Future<Result<String>> startSync(SyncSelection selection) async {
    final token = await _spotifyToken();
    if (token == null) {
      return const Result.err(
        Failure.auth(message: 'Link Spotify before syncing'),
      );
    }
    final res = await _api.ingestSpotify(
      spotifyAccessToken: token,
      sources: api.IngestSourcesBody(
        liked: selection.includeLiked,
        topTracks: selection.includeTop,
        playlistIds: selection.playlistIds.toList(),
      ),
    );
    return res.map((s) => s.jobId);
  }

  @override
  Stream<SyncProgress> syncJobStream(String jobId) async* {
    await for (final result in _api.pollIngestStatus(jobId)) {
      final status = result.valueOrNull;
      if (status == null) continue;
      yield SyncProgress(
        done: status.processed,
        total: status.total,
        added: status.addedToLibrary,
        alreadyYours: status.alreadyInLibrary,
        queued: status.queuedForAnalysis,
        currentTrack: status.currentTrack,
        finished: _isTerminal(status.status),
      );
      if (_isTerminal(status.status)) return;
    }
  }
}

// ---------------------------------------------------------------------------
// Player
// ---------------------------------------------------------------------------

class VibescapePlayerApiAdapter implements player.VibescapeApi {
  VibescapePlayerApiAdapter(this._api);
  final VibeScapeApi _api;

  @override
  Future<Result<player.PlayerTrack>> nextTrackForVibe(int vibe) async {
    final res = await _api.randomTrack(vibe: vibe.toDouble());
    return res.map(_trackToPlayerTrack);
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

QueueTrack _trackToQueue(api.Track t) => QueueTrack(
      id: (t.spotifyId ?? t.appleId?.toString() ?? t.id?.toString()) ?? '',
      title: t.title ?? '(unknown)',
      artist: t.artist ?? '',
      artUrl: t.artworkUrl,
      durationMs: t.durationMs,
      vibe: t.vibeScore?.round(),
      mood: t.mood,
      source: t.classificationSource,
      similarity: t.score,
    );

SearchResult _trackToSearchResult(api.Track t) => SearchResult(
      id: (t.spotifyId ?? t.appleId?.toString() ?? t.id?.toString()) ?? '',
      title: t.title ?? '(unknown)',
      artist: t.artist ?? '',
      origin: SearchOrigin.library,
      artUrl: t.artworkUrl,
      durationMs: t.durationMs,
      spotifyUri: t.spotifyId == null ? null : 'spotify:track:${t.spotifyId}',
      alreadyInLibrary: true,
    );

List<SyncPlaylist> _flattenLibrary(api.SpotifyLibrary lib) {
  return <SyncPlaylist>[
    SyncPlaylist(
      id: 'liked',
      name: 'Liked Songs',
      trackCount: lib.likedCount,
      kind: 'liked',
    ),
    SyncPlaylist(
      id: 'top',
      name: 'Top Tracks',
      trackCount: lib.topTracksCount,
      kind: 'top',
    ),
    ...lib.playlists.map(
      (p) => SyncPlaylist(
        id: p.id,
        name: p.name,
        trackCount: p.trackCount,
        owner: p.owner,
      ),
    ),
  ];
}

bool _isTerminal(String status) =>
    status == 'complete' || status == 'error' || status == 'cancelled';

player.PlayerTrack _trackToPlayerTrack(api.Track t) => player.PlayerTrack(
      id: (t.spotifyId ?? t.appleId?.toString() ?? t.id?.toString()) ?? '',
      title: t.title ?? '(unknown)',
      artist: t.artist ?? '',
      album: t.album,
      artworkUrl: t.artworkUrl,
      previewUrl: t.previewUrl,
      source: t.classificationSource,
      genre: t.genre,
      language: t.language,
      vibe: t.vibeScore?.round(),
      durationMs: t.durationMs ?? 30000,
    );

// keeps `Failure` reachable if consumers cross-reference; not directly used.
// ignore: unused_element
Failure _unused(Failure f) => f;
