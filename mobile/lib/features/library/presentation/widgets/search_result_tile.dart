import 'package:flutter/material.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/core/widgets/art_image.dart';
import 'package:vibescape/features/library/domain/search_result.dart';

/// A single row in the search dropdown. Shows origin badge (library /
/// Spotify) and "already yours" state for Spotify hits.
class SearchResultTile extends StatelessWidget {
  const SearchResultTile({
    super.key,
    required this.result,
    this.onTap,
    this.onAddToQueue,
  });

  final SearchResult result;
  final VoidCallback? onTap;
  final VoidCallback? onAddToQueue;

  @override
  Widget build(BuildContext context) {
    final isSpotify = result.origin == SearchOrigin.spotify;
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
              url: result.artUrl,
              size: 40,
              borderRadius: BorderRadius.circular(VibeTokens.rSm),
            ),
            const SizedBox(width: VibeTokens.s12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Flexible(
                        child: Text(
                          result.title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: VibeTokens.textPrimary,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                      const SizedBox(width: VibeTokens.s8),
                      _OriginBadge(
                        origin: result.origin,
                        alreadyInLibrary: result.alreadyInLibrary,
                      ),
                    ],
                  ),
                  const SizedBox(height: 2),
                  Text(
                    result.artist,
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
            if (onAddToQueue != null && !(isSpotify && !result.alreadyInLibrary))
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

class _OriginBadge extends StatelessWidget {
  const _OriginBadge({required this.origin, required this.alreadyInLibrary});

  final SearchOrigin origin;
  final bool alreadyInLibrary;

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (origin) {
      SearchOrigin.library => ('library', VibeTokens.accent),
      SearchOrigin.spotify =>
        alreadyInLibrary ? ('yours', VibeTokens.accent) : ('spotify', VibeTokens.moodChill),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(VibeTokens.rSm),
        border: Border.all(color: color.withValues(alpha: 0.3)),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: 9,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.3,
        ),
      ),
    );
  }
}
