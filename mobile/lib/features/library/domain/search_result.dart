import 'package:flutter/foundation.dart';

/// Origin of a search hit. Matches the two "buckets" the web dropdown shows.
enum SearchOrigin { library, spotify }

/// One row in the search dropdown.
@immutable
class SearchResult {
  const SearchResult({
    required this.id,
    required this.title,
    required this.artist,
    required this.origin,
    this.artUrl,
    this.durationMs,
    this.spotifyUri,
    this.alreadyInLibrary = false,
  });

  final String id;
  final String title;
  final String artist;
  final SearchOrigin origin;
  final String? artUrl;
  final int? durationMs;
  final String? spotifyUri;

  /// For Spotify hits: whether the track is already in the user's library.
  /// Lets the UI show "add" vs "already yours".
  final bool alreadyInLibrary;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is SearchResult &&
          other.id == id &&
          other.title == title &&
          other.artist == artist &&
          other.origin == origin &&
          other.artUrl == artUrl &&
          other.durationMs == durationMs &&
          other.spotifyUri == spotifyUri &&
          other.alreadyInLibrary == alreadyInLibrary);

  @override
  int get hashCode => Object.hash(
        id,
        title,
        artist,
        origin,
        artUrl,
        durationMs,
        spotifyUri,
        alreadyInLibrary,
      );
}
