import 'package:flutter/material.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/core/widgets/art_image.dart';
import 'package:vibescape/features/queue/domain/queue_track.dart';

/// A recommendation row. Same visual weight as [QueueItemTile] but exposes
/// an "add to queue" affordance instead of a drag handle.
class RecommendationTile extends StatelessWidget {
  const RecommendationTile({
    super.key,
    required this.track,
    this.onTap,
    this.onAddToQueue,
  });

  final QueueTrack track;
  final VoidCallback? onTap;
  final VoidCallback? onAddToQueue;

  @override
  Widget build(BuildContext context) {
    final mood = track.vibe == null ? null : VibeTokens.moodFor(track.vibe!);
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: VibeTokens.s12,
          vertical: VibeTokens.s8,
        ),
        child: Row(
          children: [
            ArtImage(
              url: track.artUrl,
              size: 40,
              glowColor: mood?.color,
              borderRadius: BorderRadius.circular(VibeTokens.rSm),
            ),
            const SizedBox(width: VibeTokens.s12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    track.title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: VibeTokens.textPrimary,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Row(
                    children: [
                      Flexible(
                        child: Text(
                          track.artist,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: VibeTokens.textSecondary,
                            fontSize: 12,
                          ),
                        ),
                      ),
                      if (track.similarity != null) ...[
                        const SizedBox(width: VibeTokens.s8),
                        Text(
                          '${(track.similarity! * 100).round()}% match',
                          style: const TextStyle(
                            color: VibeTokens.textMuted,
                            fontSize: 11,
                          ),
                        ),
                      ],
                    ],
                  ),
                ],
              ),
            ),
            IconButton(
              tooltip: 'Add to queue',
              icon: const Icon(Icons.playlist_add, size: 18),
              color: VibeTokens.accent,
              onPressed: onAddToQueue,
            ),
          ],
        ),
      ),
    );
  }
}
