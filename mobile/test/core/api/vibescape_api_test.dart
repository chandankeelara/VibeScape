import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:vibescape/core/api/vibescape_api.dart';
import 'package:vibescape/core/errors/failure.dart';
import 'package:vibescape/core/errors/result.dart';
import 'package:vibescape/core/models/similar_tracks.dart';
import 'package:vibescape/core/models/sync_job.dart';

class _MockDio extends Mock implements Dio {}

class _FakeRequestOptions extends Fake implements RequestOptions {}

class _FakeOptions extends Fake implements Options {}

Response<T> _ok<T>(String path, T data) => Response<T>(
      requestOptions: RequestOptions(path: path),
      data: data,
      statusCode: 200,
    );

DioException _dioErr(String path, int status, {Object? body}) =>
    DioException(
      requestOptions: RequestOptions(path: path),
      response: Response<Object?>(
        requestOptions: RequestOptions(path: path),
        data: body,
        statusCode: status,
      ),
      type: DioExceptionType.badResponse,
    );

void main() {
  setUpAll(() {
    registerFallbackValue(_FakeRequestOptions());
    registerFallbackValue(_FakeOptions());
  });

  late _MockDio dio;
  late VibeScapeApi api;

  setUp(() {
    dio = _MockDio();
    when(() => dio.options).thenReturn(BaseOptions(baseUrl: 'http://x'));
    api = VibeScapeApi(dio, sessionToken: 'sess-token');
  });

  group('health', () {
    test('parses a 200 response', () async {
      when(
        () => dio.get<Map<String, dynamic>>(
          '/api/health',
          options: any(named: 'options'),
        ),
      ).thenAnswer(
        (_) async => _ok('/api/health', {'status': 'ok', 'track_count': 42}),
      );

      final res = await api.health();

      expect(res.isOk, isTrue);
      expect(res.valueOrNull?.status, 'ok');
      expect(res.valueOrNull?.trackCount, 42);
    });

    test('maps 500 into a NetworkFailure', () async {
      when(
        () => dio.get<Map<String, dynamic>>(
          '/api/health',
          options: any(named: 'options'),
        ),
      ).thenThrow(_dioErr('/api/health', 500, body: {'detail': 'boom'}));

      final res = await api.health();

      expect(res.isErr, isTrue);
      expect(res.failureOrNull, isA<NetworkFailure>());
      expect((res.failureOrNull! as NetworkFailure).statusCode, 500);
    });
  });

  group('login', () {
    test('sends the expected body and parses the session token', () async {
      when(
        () => dio.post<Map<String, dynamic>>(
          '/api/auth/login',
          data: any<Object?>(named: 'data'),
          options: any(named: 'options'),
        ),
      ).thenAnswer(
        (_) async => _ok('/api/auth/login', {
          'user_id': 7,
          'display_name': 'chandan',
          'session_token': 'sess-xyz',
          'is_admin': true,
          'spotify_connected': false,
          'is_premium': false,
          'is_guest': false,
        }),
      );

      final res = await api.login(email: 'a@b.co', password: 'hunter2');

      expect(res.isOk, isTrue);
      expect(res.valueOrNull?.sessionToken, 'sess-xyz');
      expect(res.valueOrNull?.isAdmin, isTrue);
      final captured = verify(
        () => dio.post<Map<String, dynamic>>(
          '/api/auth/login',
          data: captureAny<Object?>(named: 'data'),
          options: any(named: 'options'),
        ),
      ).captured.single as Map<String, dynamic>;
      expect(captured['email'], 'a@b.co');
      expect(captured['password'], 'hunter2');
    });

    test('maps 401 into an AuthFailure', () async {
      when(
        () => dio.post<Map<String, dynamic>>(
          '/api/auth/login',
          data: any<Object?>(named: 'data'),
          options: any(named: 'options'),
        ),
      ).thenThrow(
        _dioErr(
          '/api/auth/login',
          401,
          body: {'detail': 'bad_credentials'},
        ),
      );

      final res = await api.login(email: 'a@b.co', password: 'nope');

      expect(res.isErr, isTrue);
      expect(res.failureOrNull, isA<AuthFailure>());
    });
  });

  group('searchTracks', () {
    test('unwraps the tracks array', () async {
      when(
        () => dio.get<Map<String, dynamic>>(
          '/api/tracks/search',
          queryParameters: any<Map<String, dynamic>>(named: 'queryParameters'),
          options: any(named: 'options'),
        ),
      ).thenAnswer(
        (_) async => _ok('/api/tracks/search', {
          'tracks': [
            {
              'id': 1,
              'title': 'Alpha',
              'artist': 'A',
              'spotify_id': 'sp-1',
              'vibe_score': 42.5,
            },
            {'id': 2, 'title': 'Beta', 'artist': 'B'},
          ],
        }),
      );

      final res = await api.searchTracks(query: 'alp', limit: 5);

      expect(res.isOk, isTrue);
      final tracks = res.valueOrNull!;
      expect(tracks, hasLength(2));
      expect(tracks.first.spotifyId, 'sp-1');
      expect(tracks.first.vibeScore, 42.5);
    });

    test('returns UnknownFailure when the dio call throws non-Dio', () async {
      when(
        () => dio.get<Map<String, dynamic>>(
          '/api/tracks/search',
          queryParameters: any<Map<String, dynamic>>(named: 'queryParameters'),
          options: any(named: 'options'),
        ),
      ).thenThrow(StateError('kaboom'));

      final res = await api.searchTracks(query: 'x');

      expect(res.isErr, isTrue);
      expect(res.failureOrNull, isA<UnknownFailure>());
    });
  });

  group('similarTracksPost', () {
    test('serializes weighted ids into the body', () async {
      when(
        () => dio.post<Map<String, dynamic>>(
          '/api/tracks/sp-abc/similar',
          data: any<Object?>(named: 'data'),
          options: any(named: 'options'),
        ),
      ).thenAnswer(
        (_) async => _ok('/api/tracks/sp-abc/similar', {
          'anchor': {'spotify_id': 'sp-abc', 'apple_id': 111, 'mood': 'chill'},
          'tracks': <Map<String, dynamic>>[],
          'mode_used': 'dj',
          'variant_used': 'fused',
        }),
      );

      final res = await api.similarTracksPost(
        trackKey: 'sp-abc',
        mode: 'dj',
        positiveIds: const [WeightedTrackId(id: 'sp-1', weight: 0.9)],
        negativeIds: const [WeightedTrackId(id: 'sp-2', weight: 0.4)],
        excludeIds: const ['sp-3'],
      );

      expect(res.isOk, isTrue);
      expect(res.valueOrNull?.modeUsed, 'dj');

      final body = verify(
        () => dio.post<Map<String, dynamic>>(
          '/api/tracks/sp-abc/similar',
          data: captureAny<Object?>(named: 'data'),
          options: any(named: 'options'),
        ),
      ).captured.single as Map<String, dynamic>;
      expect(body['mode'], 'dj');
      expect(body['positive_ids'], [
        {'id': 'sp-1', 'weight': 0.9},
      ]);
      expect(body['exclude_ids'], ['sp-3']);
    });
  });

  group('ingestStatus', () {
    test('parses the four bucket counters', () async {
      when(
        () => dio.get<Map<String, dynamic>>(
          '/api/ingest/status/job-1',
          options: any(named: 'options'),
        ),
      ).thenAnswer(
        (_) async => _ok('/api/ingest/status/job-1', {
          'status': 'running',
          'total': 20,
          'processed': 8,
          'added_to_library': 3,
          'already_in_library': 2,
          'queued_for_analysis': 2,
          'skipped': 1,
          'cancel_requested': false,
          'current_track': 'Alpha - A',
        }),
      );

      final res = await api.ingestStatus('job-1');

      expect(res.isOk, isTrue);
      final s = res.valueOrNull!;
      expect(s.status, 'running');
      expect(s.addedToLibrary, 3);
      expect(s.queuedForAnalysis, 2);
      expect(s.currentTrack, 'Alpha - A');
    });

    test('maps 404 into a NotFoundFailure', () async {
      when(
        () => dio.get<Map<String, dynamic>>(
          '/api/ingest/status/missing',
          options: any(named: 'options'),
        ),
      ).thenThrow(
        _dioErr(
          '/api/ingest/status/missing',
          404,
          body: {'detail': 'job not found'},
        ),
      );

      final res = await api.ingestStatus('missing');

      expect(res.isErr, isTrue);
      expect(res.failureOrNull, isA<NotFoundFailure>());
    });
  });

  group('pollIngestStatus', () {
    test('emits snapshots until a terminal status is reached', () async {
      final replies = <Map<String, dynamic>>[
        {'status': 'running', 'processed': 1, 'total': 3},
        {'status': 'running', 'processed': 2, 'total': 3},
        {'status': 'complete', 'processed': 3, 'total': 3},
      ];
      var call = 0;
      when(
        () => dio.get<Map<String, dynamic>>(
          '/api/ingest/status/job-2',
          options: any(named: 'options'),
        ),
      ).thenAnswer((_) async {
        final data = replies[call++];
        return _ok('/api/ingest/status/job-2', data);
      });

      final events = <SyncJobStatus>[];
      await for (final ev in api.pollIngestStatus(
        'job-2',
        interval: const Duration(milliseconds: 1),
      )) {
        if (ev is Ok<SyncJobStatus>) events.add(ev.value);
      }

      expect(events, hasLength(3));
      expect(events.last.status, 'complete');
    });
  });

  group('spotifyLibrary', () {
    test('forwards the X-Spotify-Authorization header', () async {
      when(
        () => dio.get<Map<String, dynamic>>(
          '/api/spotify/library',
          options: any(named: 'options'),
        ),
      ).thenAnswer(
        (_) async => _ok('/api/spotify/library', {
          'liked_count': 12,
          'top_tracks_count': 5,
          'playlists': [
            {
              'id': 'pl-1',
              'name': 'Chill',
              'track_count': 30,
              'owner': 'me',
              'owned_by_me': true,
            },
          ],
        }),
      );

      final res = await api.spotifyLibrary(spotifyAccessToken: 'sp-tok');

      expect(res.isOk, isTrue);
      expect(res.valueOrNull?.likedCount, 12);
      expect(res.valueOrNull?.playlists.first.trackCount, 30);

      final capturedOpts = verify(
        () => dio.get<Map<String, dynamic>>(
          '/api/spotify/library',
          options: captureAny<Options?>(named: 'options'),
        ),
      ).captured.single as Options;
      expect(
        capturedOpts.headers?['X-Spotify-Authorization'],
        'Bearer sp-tok',
      );
      expect(capturedOpts.headers?['Authorization'], 'Bearer sess-token');
    });
  });
}
