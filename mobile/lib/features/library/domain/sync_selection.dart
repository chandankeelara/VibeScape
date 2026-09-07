import 'package:flutter/foundation.dart';

/// One row in the "Sync my library" tab: a Spotify playlist (or the special
/// synthetic "Liked" / "Top" pseudo-playlists) that the user can tick.
@immutable
class SyncPlaylist {
  const SyncPlaylist({
    required this.id,
    required this.name,
    required this.trackCount,
    this.owner,
    this.artUrl,
    this.kind = 'playlist',
  });

  final String id;
  final String name;
  final int trackCount;
  final String? owner;
  final String? artUrl;

  /// 'playlist' | 'liked' | 'top' — synthetic rows carry a different kind.
  final String kind;
}

/// The user's selection inside the sync modal. Passed to `startSync`.
@immutable
class SyncSelection {
  const SyncSelection({
    this.playlistIds = const {},
    this.includeLiked = false,
    this.includeTop = false,
    this.publicPlaylistUrl,
  });

  final Set<String> playlistIds;
  final bool includeLiked;
  final bool includeTop;

  /// Only used for the "Add public playlist" tab.
  final String? publicPlaylistUrl;

  bool get isEmpty =>
      playlistIds.isEmpty &&
      !includeLiked &&
      !includeTop &&
      (publicPlaylistUrl == null || publicPlaylistUrl!.isEmpty);

  SyncSelection copyWith({
    Set<String>? playlistIds,
    bool? includeLiked,
    bool? includeTop,
    String? publicPlaylistUrl,
    bool clearUrl = false,
  }) {
    return SyncSelection(
      playlistIds: playlistIds ?? this.playlistIds,
      includeLiked: includeLiked ?? this.includeLiked,
      includeTop: includeTop ?? this.includeTop,
      publicPlaylistUrl:
          clearUrl ? null : (publicPlaylistUrl ?? this.publicPlaylistUrl),
    );
  }
}

/// Live progress emitted by the backend sync job stream.
@immutable
class SyncProgress {
  const SyncProgress({
    required this.done,
    required this.total,
    required this.added,
    required this.alreadyYours,
    required this.queued,
    this.currentTrack,
    this.finished = false,
  });

  final int done;
  final int total;
  final int added;
  final int alreadyYours;
  final int queued;
  final String? currentTrack;
  final bool finished;

  /// 0.0..1.0. Returns 0 when total is 0 (avoids div-by-zero flash).
  double get fraction => total == 0 ? 0 : (done / total).clamp(0.0, 1.0);
}
