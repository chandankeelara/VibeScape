import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:vibescape/core/theme/tokens.dart';

/// Album/track artwork with a soft glow. Reused by player, queue, search.
class ArtImage extends StatelessWidget {
  const ArtImage({
    super.key,
    required this.url,
    this.size = 96,
    this.glowColor,
    this.borderRadius,
  });

  final String? url;
  final double size;
  final Color? glowColor;
  final BorderRadius? borderRadius;

  @override
  Widget build(BuildContext context) {
    final radius = borderRadius ?? BorderRadius.circular(VibeTokens.rMd);
    final glow = glowColor ?? VibeTokens.moodSteady;
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        borderRadius: radius,
        boxShadow: [
          BoxShadow(
            color: glow.withValues(alpha: 0.35),
            blurRadius: size * 0.35,
            spreadRadius: -size * 0.1,
          ),
        ],
      ),
      child: ClipRRect(
        borderRadius: radius,
        child: url == null || url!.isEmpty
            ? _placeholder()
            : CachedNetworkImage(
                imageUrl: url!,
                fit: BoxFit.cover,
                placeholder: (_, __) => _placeholder(),
                errorWidget: (_, __, ___) => _placeholder(),
              ),
      ),
    );
  }

  Widget _placeholder() => Container(
        color: VibeTokens.surface,
        child: const Icon(Icons.music_note, color: VibeTokens.textMuted),
      );
}
