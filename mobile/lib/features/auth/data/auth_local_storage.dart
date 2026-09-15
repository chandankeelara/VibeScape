import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Wraps the two device-local persistence mechanisms used by auth:
///
/// * `SharedPreferences` — non-sensitive breadcrumb about which profile last
///   signed in on this device (surfaces the correct default in the picker).
/// * `FlutterSecureStorage` — the VibeScape session bearer token and, later,
///   per-profile Spotify tokens.
///
/// Anything that reads or writes those keys should go through this class so
/// the storage schema stays in one place.
class AuthLocalStorage {
  AuthLocalStorage({
    required SharedPreferences prefs,
    required FlutterSecureStorage secure,
  })  : _prefs = prefs,
        _secure = secure;

  final SharedPreferences _prefs;
  final FlutterSecureStorage _secure;

  static const _kLastUserId = 'vibescape_user_id';
  static const _kSessionToken = 'vibescape_session_token';
  static String _spotifyTokenKey(String userId) =>
      'vibescape_spotify_token_$userId';

  Future<void> setLastUserId(String userId) =>
      _prefs.setString(_kLastUserId, userId);

  String? getLastUserId() => _prefs.getString(_kLastUserId);

  Future<void> clearLastUserId() => _prefs.remove(_kLastUserId);

  Future<void> setSessionToken(String token) =>
      _secure.write(key: _kSessionToken, value: token);

  Future<String?> getSessionToken() => _secure.read(key: _kSessionToken);

  Future<void> clearSessionToken() => _secure.delete(key: _kSessionToken);

  Future<void> setSpotifyToken(String userId, String token) =>
      _secure.write(key: _spotifyTokenKey(userId), value: token);

  Future<String?> getSpotifyToken(String userId) =>
      _secure.read(key: _spotifyTokenKey(userId));

  Future<void> clearSpotifyToken(String userId) =>
      _secure.delete(key: _spotifyTokenKey(userId));
}

/// Async provider so callers `ref.watch(authLocalStorageProvider.future)`.
///
/// SharedPreferences returns a Future — resolving it inside the provider
/// avoids sprinkling `.getInstance()` across the codebase.
final authLocalStorageProvider = FutureProvider<AuthLocalStorage>((ref) async {
  final prefs = await SharedPreferences.getInstance();
  const secure = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );
  return AuthLocalStorage(prefs: prefs, secure: secure);
});
