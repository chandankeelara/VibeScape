import 'package:flutter/material.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/core/widgets/art_image.dart';
import 'package:vibescape/features/queue/domain/queue_track.dart';

/// A single row inside the "Up next" list. Meant to live inside a
/// `ReorderableListView`, so it exposes a drag handle on the trailing edge.
class QueueItemTile extends StatelessWidget {
  const QueueItemTile({
    super.key,
    required this.track,
    required this.index,
    required this.isCurrent,
    this.onTap,
    this.onRemove,
  });

  final QueueTrack track;
  final int index;
  final bool isCurrent;
  final VoidCallback? onTap;
  final VoidCallback? onRemove;

  @override
  Widget build(BuildContext context) {
    final mood = track.vibe == null ? null : VibeTokens.moodFor(track.vibe!);
    return Material(
      color: isCurrent
          ? VibeTokens.surfaceHi.withValues(alpha: 0.85)
          : Colors.transparent,
      child: InkWell(
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
                size: 44,
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
                    Text(
                      track.artist,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: VibeTokens.textSecondary,
                        fontSize: 12,
                      ),
                    ),
                  ],
                ),
              ),
              if (onRemove != null)
                IconButton(
                  tooltip: 'Remove from queue',
                  icon: const Icon(Icons.close, size: 16),
                  color: VibeTokens.textMuted,
                  onPressed: onRemove,
                ),
              ReorderableDragStartListener(
                index: index,
                child: const Padding(
                  padding: EdgeInsets.symmetric(horizontal: VibeTokens.s4),
                  child: Icon(
                    Icons.drag_indicator,
                    size: 18,
                    color: VibeTokens.textMuted,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
