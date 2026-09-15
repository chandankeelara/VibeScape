import 'package:freezed_annotation/freezed_annotation.dart';

part 'track_spotify.freezed.dart';
part 'track_spotify.g.dart';

/// Response for `GET /api/track/{apple_id}/spotify` — the Spotify id + URI
/// for a track in the caller's library, or 404 if none is linked.
@freezed
abstract class TrackSpotifyLink with _$TrackSpotifyLink {
  @JsonSerializable(fieldRename: FieldRename.snake)
  const factory TrackSpotifyLink({
    required String spotifyId,
    required String uri,
  }) = _TrackSpotifyLink;

  factory TrackSpotifyLink.fromJson(Map<String, dynamic> json) =>
      _$TrackSpotifyLinkFromJson(json);
}
