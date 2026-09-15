import 'package:freezed_annotation/freezed_annotation.dart';

part 'failure.freezed.dart';

@freezed
sealed class Failure with _$Failure {
  const factory Failure.network({required String message, int? statusCode}) = NetworkFailure;
  const factory Failure.auth({required String message}) = AuthFailure;
  const factory Failure.notFound({required String message}) = NotFoundFailure;
  const factory Failure.validation({required String message}) = ValidationFailure;
  const factory Failure.unknown({required String message, Object? cause}) = UnknownFailure;
}
