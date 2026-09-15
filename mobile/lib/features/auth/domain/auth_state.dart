import 'package:freezed_annotation/freezed_annotation.dart';
import 'package:vibescape/core/errors/failure.dart';
import 'package:vibescape/features/auth/domain/session.dart';

part 'auth_state.freezed.dart';

/// Finite states of the auth flow, matching `frontend/login.html`.
///
///   signedOut → (Spotify | email | guest CTA) → busy → authenticated
///                                                    → failed(reason)
@freezed
sealed class AuthState with _$AuthState {
  const factory AuthState.signedOut() = SignedOut;
  const factory AuthState.busy({@Default('') String hint}) = Busy;
  const factory AuthState.authenticated({required Session session}) =
      Authenticated;
  const factory AuthState.failed({required Failure failure}) = AuthFailed;
}
