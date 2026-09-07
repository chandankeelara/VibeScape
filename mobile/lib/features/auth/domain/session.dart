import 'package:freezed_annotation/freezed_annotation.dart';
import 'package:vibescape/features/auth/domain/profile.dart';

part 'session.freezed.dart';

/// An authenticated VibeScape session: bearer token + the signed-in profile.
@freezed
abstract class Session with _$Session {
  const factory Session({
    required String token,
    required Profile profile,
  }) = _Session;
}
