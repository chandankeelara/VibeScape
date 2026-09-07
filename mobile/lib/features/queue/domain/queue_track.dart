import 'package:flutter/foundation.dart';

/// Minimal track shape used by the queue + recommendations UI. Matches
/// the fields the web app's `renderQueueRow` reads from a track object.
///
/// This deliberately does NOT reuse a broader `Track` model — the queue only
/// needs a display-oriented subset. When the shared `Track` model lands in
/// `lib/core/models/`, add a `QueueTrack.fromTrack(track)` factory.
@immutable
class QueueTrack {
  const QueueTrack({
    required this.id,
    required this.title,
    required this.artist,
    this.artUrl,
    this.durationMs,
    this.vibe,
    this.mood,
    this.source,
    this.similarity,
  });

  final String id;
  final String title;
  final String artist;
  final String? artUrl;
  final int? durationMs;

  /// 0-100 vibe score, or null if not yet classified.
  final int? vibe;

  /// e.g. 'chill', 'hype' — mirrors backend classification labels.
  final String? mood;

  /// Where the track came from: 'library', 'spotify', 'recommendation'.
  final String? source;

  /// Cosine similarity from the recs endpoint (0..1). Only set for
  /// tracks surfaced in the "Recommended for this track" section.
  final double? similarity;

  QueueTrack copyWith({
    String? id,
    String? title,
    String? artist,
    String? artUrl,
    int? durationMs,
    int? vibe,
    String? mood,
    String? source,
    double? similarity,
  }) {
    return QueueTrack(
      id: id ?? this.id,
      title: title ?? this.title,
      artist: artist ?? this.artist,
      artUrl: artUrl ?? this.artUrl,
      durationMs: durationMs ?? this.durationMs,
      vibe: vibe ?? this.vibe,
      mood: mood ?? this.mood,
      source: source ?? this.source,
      similarity: similarity ?? this.similarity,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is QueueTrack &&
          other.id == id &&
          other.title == title &&
          other.artist == artist &&
          other.artUrl == artUrl &&
          other.durationMs == durationMs &&
          other.vibe == vibe &&
          other.mood == mood &&
          other.source == source &&
          other.similarity == similarity);

  @override
  int get hashCode => Object.hash(
        id,
        title,
        artist,
        artUrl,
        durationMs,
        vibe,
        mood,
        source,
        similarity,
      );
}
