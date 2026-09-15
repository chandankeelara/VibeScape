import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/api/dio_client.dart';
import 'package:vibescape/core/errors/failure.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/core/models/admin.dart';
import 'package:vibescape/core/models/health.dart';
import 'package:vibescape/core/models/ingest_clear.dart';
import 'package:vibescape/core/models/ingest_single.dart';
import 'package:vibescape/core/models/mood.dart';
import 'package:vibescape/core/models/recompute.dart';
import 'package:vibescape/core/models/session_user.dart';
import 'package:vibescape/core/models/similar_tracks.dart';
import 'package:vibescape/core/models/spotify_auth.dart';
import 'package:vibescape/core/models/spotify_library.dart';
import 'package:vibescape/core/models/spotify_search.dart';
import 'package:vibescape/core/models/sync_job.dart';
import 'package:vibescape/core/models/track.dart';
import 'package:vibescape/core/models/track_features.dart';
import 'package:vibescape/core/models/track_spotify.dart';
import 'package:vibescape/core/models/youtube.dart';

/// The single typed entry point to the VibeScape FastAPI backend.
///
/// Every method returns a [Result] — errors never escape as exceptions.
/// Auth is passed via [sessionToken]; construct a new instance (or use the
/// helper [withSessionToken]) when the session changes.
///
/// Endpoints that need the caller's raw Spotify OAuth token accept it via
/// the `spotifyAccessToken` parameter and forward it in the
/// `X-Spotify-Authorization` header the backend expects.
class VibeScapeApi {
  VibeScapeApi(this._dio, {this.sessionToken});

  final Dio _dio;
  final String? sessionToken;

  /// Return a copy of this client bound to a different (or absent) session.
  VibeScapeApi withSessionToken(String? token) =>
      VibeScapeApi(_dio, sessionToken: token);

  // ---------------------------------------------------------------------------
  // Health + config
  // ---------------------------------------------------------------------------

  Future<Result<HealthStatus>> health() =>
      _get('/api/health', HealthStatus.fromJson);

  Future<Result<SpotifyConfig>> spotifyConfig() =>
      _get('/api/spotify/config', SpotifyConfig.fromJson);

  Future<Result<ClientConfig>> clientConfig() =>
      _get('/api/client-config', ClientConfig.fromJson);

  Future<Result<List<String>>> moods() => _wrap(() async {
        final res = await _dio.get<Map<String, dynamic>>(
          '/api/moods',
          options: _authOptions(),
        );
        final list = (res.data?['moods'] as List?) ?? const [];
        return list.map((e) => e.toString()).toList(growable: false);
      });

  Future<Result<DemoMoodsResponse>> demoMoods() =>
      _get('/api/demo/moods', DemoMoodsResponse.fromJson);

  // ---------------------------------------------------------------------------
  // Auth
  // ---------------------------------------------------------------------------

  Future<Result<AuthResponse>> signup({
    required String email,
    required String password,
  }) =>
      _post(
        '/api/auth/signup',
        {'email': email, 'password': password},
        AuthResponse.fromJson,
      );

  Future<Result<AuthResponse>> login({
    required String email,
    required String password,
  }) =>
      _post(
        '/api/auth/login',
        {'email': email, 'password': password},
        AuthResponse.fromJson,
      );

  Future<Result<AuthResponse>> guest() =>
      _post('/api/auth/guest', const <String, dynamic>{}, AuthResponse.fromJson);

  Future<Result<AuthResponse>> spotifyOauth({
    required String code,
    String? redirectUri,
  }) =>
      _post(
        '/api/auth/spotify-oauth',
        {
          'code': code,
          if (redirectUri != null) 'redirect_uri': redirectUri,
        },
        AuthResponse.fromJson,
      );

  Future<Result<SpotifyRefreshResponse>> spotifyRefresh({
    required String refreshToken,
  }) =>
      _post(
        '/api/spotify/refresh',
        {'refresh_token': refreshToken},
        SpotifyRefreshResponse.fromJson,
      );

  Future<Result<void>> logout() => _wrap(() async {
        await _dio.post<void>(
          '/api/auth/logout',
          options: _authOptions(),
        );
      });

  Future<Result<MeResponse>> me() => _get('/api/auth/me', MeResponse.fromJson);

  Future<Result<Map<String, dynamic>>> spotifyLink({
    required String spotifyUserId,
    String? spotifyDisplayName,
  }) =>
      _wrap(() async {
        final res = await _dio.post<Map<String, dynamic>>(
          '/api/auth/spotify-link',
          data: {
            'spotify_user_id': spotifyUserId,
            if (spotifyDisplayName != null)
              'spotify_display_name': spotifyDisplayName,
          },
          options: _authOptions(),
        );
        return res.data ?? const {};
      });

  // ---------------------------------------------------------------------------
  // Tracks / recommendations
  // ---------------------------------------------------------------------------

  Future<Result<List<Track>>> listTracks({
    double vibeMin = 0,
    double vibeMax = 100,
    int limit = 20,
    String? mood,
    bool shuffle = false,
  }) =>
      _wrap(() async {
        final res = await _dio.get<List<dynamic>>(
          '/api/tracks',
          queryParameters: {
            'vibe_min': vibeMin,
            'vibe_max': vibeMax,
            'limit': limit,
            'shuffle': shuffle,
            if (mood != null) 'mood': mood,
          },
          options: _authOptions(),
        );
        return (res.data ?? const [])
            .map((e) => Track.fromJson(e as Map<String, dynamic>))
            .toList(growable: false);
      });

  Future<Result<List<Track>>> searchTracks({
    required String query,
    int limit = 15,
  }) =>
      _wrap(() async {
        final res = await _dio.get<Map<String, dynamic>>(
          '/api/tracks/search',
          queryParameters: {'q': query, 'limit': limit},
          options: _authOptions(),
        );
        final tracks = (res.data?['tracks'] as List?) ?? const [];
        return tracks
            .map((e) => Track.fromJson(e as Map<String, dynamic>))
            .toList(growable: false);
      });

  Future<Result<Track>> randomTrack({
    required double vibe,
    double tolerance = 12,
    List<int> excludeAppleIds = const [],
  }) =>
      _wrap(() async {
        final res = await _dio.get<Map<String, dynamic>>(
          '/api/tracks/random',
          queryParameters: {
            'vibe': vibe,
            'tolerance': tolerance,
            if (excludeAppleIds.isNotEmpty)
              'exclude_ids': excludeAppleIds.join(','),
          },
          options: _authOptions(),
        );
        return Track.fromJson(res.data ?? const {});
      });

  Future<Result<SimilarTracksResponse>> similarTracks({
    required String trackKey,
    int limit = 8,
  }) =>
      _wrap(() async {
        final res = await _dio.get<Map<String, dynamic>>(
          '/api/tracks/$trackKey/similar',
          queryParameters: {'limit': limit},
          options: _authOptions(),
        );
        return SimilarTracksResponse.fromJson(res.data ?? const {});
      });

  /// POST variant of similar-tracks, used to drive DJ mode with a
  /// session-weighted taste vector.
  Future<Result<SimilarTracksResponse>> similarTracksPost({
    required String trackKey,
    String mode = 'vibe',
    List<WeightedTrackId> positiveIds = const [],
    List<WeightedTrackId> negativeIds = const [],
    List<String> excludeIds = const [],
    int limit = 8,
    String? variant,
  }) =>
      _wrap(() async {
        final res = await _dio.post<Map<String, dynamic>>(
          '/api/tracks/$trackKey/similar',
          data: {
            'mode': mode,
            'positive_ids': positiveIds.map((e) => e.toJson()).toList(),
            'negative_ids': negativeIds.map((e) => e.toJson()).toList(),
            'exclude_ids': excludeIds,
            'limit': limit,
            if (variant != null) 'variant': variant,
          },
          options: _authOptions(),
        );
        return SimilarTracksResponse.fromJson(res.data ?? const {});
      });

  Future<Result<TrackFeaturesResponse>> trackFeatures(String trackKey) =>
      _get(
        '/api/tracks/$trackKey/features',
        TrackFeaturesResponse.fromJson,
      );

  Future<Result<TrackSpotifyLink>> trackSpotifyLink(int appleId) => _get(
        '/api/track/$appleId/spotify',
        TrackSpotifyLink.fromJson,
      );

  Future<Result<RecomputeSummary>> recomputeScores() =>
      _post('/api/recompute-scores', const {}, RecomputeSummary.fromJson);

  // ---------------------------------------------------------------------------
  // YouTube
  // ---------------------------------------------------------------------------

  Future<Result<YouTubeLookup>> youtubeLookup(int trackId) => _get(
        '/api/tracks/$trackId/youtube',
        YouTubeLookup.fromJson,
      );

  Future<Result<List<YouTubeSearchResult>>> youtubeSearch({
    required int trackId,
    required String query,
    int limit = 5,
  }) =>
      _wrap(() async {
        final res = await _dio.get<Map<String, dynamic>>(
          '/api/tracks/$trackId/youtube/search',
          queryParameters: {'q': query, 'limit': limit},
          options: _authOptions(),
        );
        final results = (res.data?['results'] as List?) ?? const [];
        return results
            .map((e) => YouTubeSearchResult.fromJson(e as Map<String, dynamic>))
            .toList(growable: false);
      });

  Future<Result<YouTubeLookup>> setYoutubeId({
    required int trackId,
    required String youtubeId,
  }) =>
      _post(
        '/api/tracks/$trackId/youtube',
        {'youtube_id': youtubeId},
        YouTubeLookup.fromJson,
      );

  // ---------------------------------------------------------------------------
  // Spotify (server-mediated)
  // ---------------------------------------------------------------------------

  Future<Result<SpotifyLibrary>> spotifyLibrary({
    required String spotifyAccessToken,
  }) =>
      _wrap(() async {
        final res = await _dio.get<Map<String, dynamic>>(
          '/api/spotify/library',
          options: _authOptions(
            extraHeaders: {
              'X-Spotify-Authorization': 'Bearer $spotifyAccessToken',
            },
          ),
        );
        return SpotifyLibrary.fromJson(res.data ?? const {});
      });

  Future<Result<List<SpotifySearchResult>>> spotifySearch({
    required String query,
    required String spotifyAccessToken,
    int limit = 10,
  }) =>
      _wrap(() async {
        final res = await _dio.get<Map<String, dynamic>>(
          '/api/spotify/search',
          queryParameters: {'q': query, 'limit': limit},
          options: _authOptions(
            extraHeaders: {
              'X-Spotify-Authorization': 'Bearer $spotifyAccessToken',
            },
          ),
        );
        final list = (res.data?['tracks'] as List?) ?? const [];
        return list
            .map(
              (e) => SpotifySearchResult.fromJson(e as Map<String, dynamic>),
            )
            .toList(growable: false);
      });

  // ---------------------------------------------------------------------------
  // Ingest
  // ---------------------------------------------------------------------------

  Future<Result<IngestClearResult>> ingestClear() =>
      _post('/api/ingest/clear', const {}, IngestClearResult.fromJson);

  Future<Result<SingleIngestResponse>> ingestSingle({
    required String spotifyId,
    String? spotifyAccessToken,
  }) =>
      _post(
        '/api/ingest/single',
        {
          'spotify_id': spotifyId,
          if (spotifyAccessToken != null) 'access_token': spotifyAccessToken,
        },
        SingleIngestResponse.fromJson,
      );

  Future<Result<SyncJobStart>> ingestSpotify({
    required String spotifyAccessToken,
    required IngestSourcesBody sources,
  }) =>
      _post(
        '/api/ingest/spotify',
        {
          'access_token': spotifyAccessToken,
          'sources': sources.toJson(),
        },
        SyncJobStart.fromJson,
      );

  Future<Result<SyncJobStart>> ingestSpotifyPublic({
    String? playlistUrl,
    String? playlistId,
    String? spotifyAccessToken,
  }) =>
      _post(
        '/api/ingest/spotify-public',
        {
          if (playlistUrl != null) 'playlist_url': playlistUrl,
          if (playlistId != null) 'playlist_id': playlistId,
          if (spotifyAccessToken != null) 'access_token': spotifyAccessToken,
        },
        SyncJobStart.fromJson,
      );

  Future<Result<SyncJobStatus>> ingestStatus(String jobId) =>
      _get('/api/ingest/status/$jobId', SyncJobStatus.fromJson);

  Future<Result<void>> cancelIngest(String jobId) => _wrap(() async {
        await _dio.delete<void>(
          '/api/ingest/status/$jobId',
          options: _authOptions(),
        );
      });

  /// Polls [ingestStatus] every [interval] and emits every snapshot until
  /// the job reaches a terminal state ('complete', 'error', 'cancelled') or
  /// the subscription is cancelled.
  ///
  /// Errors are surfaced as `Result.err` events on the stream — the stream
  /// itself never throws.
  Stream<Result<SyncJobStatus>> pollIngestStatus(
    String jobId, {
    Duration interval = const Duration(milliseconds: 500),
  }) async* {
    const terminal = {'complete', 'error', 'cancelled'};
    while (true) {
      final snapshot = await ingestStatus(jobId);
      yield snapshot;
      final done = switch (snapshot) {
        Ok<SyncJobStatus>(:final value) => terminal.contains(value.status),
        Err<SyncJobStatus>() => true,
      };
      if (done) return;
      await Future<void>.delayed(interval);
    }
  }

  // ---------------------------------------------------------------------------
  // Admin
  // ---------------------------------------------------------------------------

  Future<Result<List<AdminUser>>> adminUsers() => _wrap(() async {
        final res = await _dio.get<Map<String, dynamic>>(
          '/api/admin/users',
          options: _authOptions(),
        );
        final list = (res.data?['users'] as List?) ?? const [];
        return list
            .map((e) => AdminUser.fromJson(e as Map<String, dynamic>))
            .toList(growable: false);
      });

  Future<Result<AdminUserStats>> adminUserStats(int userId) => _get(
        '/api/admin/users/$userId/stats',
        AdminUserStats.fromJson,
      );

  Future<Result<List<AdminUserTrack>>> adminUserTracks({
    required int userId,
    int limit = 50,
    int offset = 0,
  }) =>
      _wrap(() async {
        final res = await _dio.get<Map<String, dynamic>>(
          '/api/admin/users/$userId/tracks',
          queryParameters: {'limit': limit, 'offset': offset},
          options: _authOptions(),
        );
        final list = (res.data?['tracks'] as List?) ?? const [];
        return list
            .map((e) => AdminUserTrack.fromJson(e as Map<String, dynamic>))
            .toList(growable: false);
      });

  Future<Result<void>> adminDeleteUser(int userId) => _wrap(() async {
        await _dio.delete<void>(
          '/api/admin/users/$userId',
          options: _authOptions(),
        );
      });

  // ---------------------------------------------------------------------------
  // Streaming URLs (no request — just URL construction)
  // ---------------------------------------------------------------------------

  /// URL for the range-capable audio stream for [trackKey] (spotify_id).
  /// The backend accepts the session token as a `?token=` query param since
  /// `<audio>` / `AudioPlayer` cannot set headers on their src fetch.
  Uri streamUrlByKey(String trackKey) => _streamUri(
        '/api/stream/${Uri.encodeComponent(trackKey)}',
      );

  /// URL for the range-capable audio stream by Spotify id.
  Uri spotifyStreamUrl(String spotifyId) => _streamUri(
        '/api/stream/spotify/${Uri.encodeComponent(spotifyId)}',
      );

  Uri _streamUri(String path) {
    final base = Uri.parse(_dio.options.baseUrl);
    final basePath = base.path.endsWith('/')
        ? base.path.substring(0, base.path.length - 1)
        : base.path;
    return base.replace(
      path: '$basePath$path',
      queryParameters: sessionToken == null ? null : {'token': sessionToken},
    );
  }

  // ---------------------------------------------------------------------------
  // Internals
  // ---------------------------------------------------------------------------

  Options _authOptions({Map<String, String>? extraHeaders}) {
    final headers = <String, String>{
      if (sessionToken != null) 'Authorization': 'Bearer $sessionToken',
      ...?extraHeaders,
    };
    return Options(headers: headers);
  }

  Future<Result<T>> _get<T>(
    String path,
    T Function(Map<String, dynamic> json) parser,
  ) =>
      _wrap(() async {
        final res = await _dio.get<Map<String, dynamic>>(
          path,
          options: _authOptions(),
        );
        return parser(res.data ?? const {});
      });

  Future<Result<T>> _post<T>(
    String path,
    Object body,
    T Function(Map<String, dynamic> json) parser,
  ) =>
      _wrap(() async {
        final res = await _dio.post<Map<String, dynamic>>(
          path,
          data: body,
          options: _authOptions(),
        );
        return parser(res.data ?? const {});
      });

  Future<Result<T>> _wrap<T>(Future<T> Function() run) async {
    try {
      final value = await run();
      return Result.ok(value);
    } on DioException catch (e) {
      return Result.err(failureFromDioError(e));
    } catch (e) {
      return Result.err(Failure.unknown(message: e.toString(), cause: e));
    }
  }
}

/// Riverpod provider for a [VibeScapeApi] instance bound to the current
/// [dioProvider]. Session token defaults to null; the auth controller
/// overrides this provider when the user signs in.
final vibeScapeApiProvider = Provider<VibeScapeApi>((ref) {
  final dio = ref.watch(dioProvider);
  return VibeScapeApi(dio);
});
