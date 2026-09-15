import 'package:flutter/material.dart';
import 'package:vibescape/core/theme/tokens.dart';

/// Animated mood label. Crossfades label + color when [vibe] crosses into
/// a new mood bucket. Uses [AnimatedSwitcher] internally so consumers just
/// pass the current vibe value.
class MoodLabel extends StatelessWidget {
  const MoodLabel({
    super.key,
    required this.vibe,
    this.style,
  });

  final int vibe;
  final TextStyle? style;

  @override
  Widget build(BuildContext context) {
    final mood = VibeTokens.moodFor(vibe);
    final base = style ??
        Theme.of(context).textTheme.titleLarge?.copyWith(
              fontWeight: FontWeight.w600,
              letterSpacing: 0.6,
            );
    return AnimatedSwitcher(
      duration: VibeTokens.dMed,
      switchInCurve: Curves.easeOutCubic,
      switchOutCurve: Curves.easeInCubic,
      transitionBuilder: (child, anim) => FadeTransition(
        opacity: anim,
        child: SlideTransition(
          position: Tween<Offset>(
            begin: const Offset(0, 0.15),
            end: Offset.zero,
          ).animate(anim),
          child: child,
        ),
      ),
      child: AnimatedDefaultTextStyle(
        key: ValueKey<String>(mood.label),
        duration: VibeTokens.dMed,
        curve: Curves.easeOutCubic,
        style: (base ?? const TextStyle()).copyWith(color: mood.color),
        child: Text(mood.label),
      ),
    );
  }
}
