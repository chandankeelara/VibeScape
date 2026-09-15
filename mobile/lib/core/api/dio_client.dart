import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/env/env.dart';
import 'package:vibescape/core/errors/failure.dart';

/// Central dio instance. Feature repositories should NOT create their own —
/// depend on `dioProvider` so tests can swap in a `DioAdapter` mock.
final dioProvider = Provider<Dio>((ref) {
  final env = ref.watch(envProvider);
  final dio = Dio(
    BaseOptions(
      baseUrl: env.apiBaseUrl,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 20),
      sendTimeout: const Duration(seconds: 20),
      headers: {'Content-Type': 'application/json'},
    ),
  );

  dio.interceptors.add(
    InterceptorsWrapper(
      onError: (e, handler) {
        // Normalize dio errors so callers can pattern-match on `Failure`.
        handler.next(e);
      },
    ),
  );

  return dio;
});

/// Convert a DioException into our sealed `Failure` type.
Failure failureFromDioError(DioException e) {
  final status = e.response?.statusCode;
  final data = e.response?.data;
  final serverMsg = (data is Map && data['detail'] is String) ? data['detail'] as String : null;

  if (status == 401 || status == 403) {
    return Failure.auth(message: serverMsg ?? 'Unauthorized');
  }
  if (status == 404) {
    return Failure.notFound(message: serverMsg ?? 'Not found');
  }
  if (status == 422) {
    return Failure.validation(message: serverMsg ?? 'Invalid request');
  }
  if (status != null && status >= 500 && status < 600) {
    return Failure.network(
      message: serverMsg ?? 'Server error',
      statusCode: status,
    );
  }
  return Failure.network(
    message: e.message ?? 'Network error',
    statusCode: status,
  );
}
