import 'package:flutter/material.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/core/widgets/art_image.dart';

/// Hero album art with a mood-tinted glow that pulses subtly while playing.
/// Uses a single [AnimationController] driving both scale and glow alpha.
class AlbumArtHero extends StatefulWidget {
  const AlbumArtHero({
    super.key,
    required this.artworkUrl,
    required this.moodColor,
    required this.isPlaying,
    this.size = 280,
    this.heroTag,
  });

  final String? artworkUrl;
  final Color moodColor;
  final bool isPlaying;
  final double size;
  final Object? heroTag;

  @override
  State<AlbumArtHero> createState() => _AlbumArtHeroState();
}

class _AlbumArtHeroState extends State<AlbumArtHero>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulse;

  @override
  void initState() {
    super.initState();
    _pulse = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 2400),
      lowerBound: 0,
      upperBound: 1,
    );
    if (widget.isPlaying) _pulse.repeat(reverse: true);
  }

  @override
  void didUpdateWidget(covariant AlbumArtHero old) {
    super.didUpdateWidget(old);
    if (widget.isPlaying && !_pulse.isAnimating) {
      _pulse.repeat(reverse: true);
    } else if (!widget.isPlaying && _pulse.isAnimating) {
      _pulse.stop();
      _pulse.animateTo(0, duration: VibeTokens.dMed, curve: Curves.easeOut);
    }
  }

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final art = AnimatedBuilder(
      animation: _pulse,
      builder: (context, _) {
        final t = Curves.easeInOut.transform(_pulse.value);
        final scale = 1.0 + (widget.isPlaying ? 0.015 * t : 0);
        final glowAlpha = 0.35 + (widget.isPlaying ? 0.25 * t : 0);
        return Transform.scale(
          scale: scale,
          child: Container(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(VibeTokens.rXl),
              boxShadow: [
                BoxShadow(
                  color: widget.moodColor.withValues(alpha: glowAlpha),
                  blurRadius: widget.size * 0.42,
                  spreadRadius: -widget.size * 0.08,
                ),
              ],
            ),
            child: ArtImage(
              url: widget.artworkUrl,
              size: widget.size,
              glowColor: Colors.transparent,
              borderRadius: BorderRadius.circular(VibeTokens.rXl),
            ),
          ),
        );
      },
    );

    if (widget.heroTag == null) return art;
    return Hero(tag: widget.heroTag!, child: art);
  }
}
