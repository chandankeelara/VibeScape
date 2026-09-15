import 'package:flutter/material.dart';
import 'package:vibescape/core/theme/tokens.dart';

/// Animated pill toggle that mirrors the web `.dj-toggle`. The inner dot
/// slides between left/right positions and the pill background crossfades
/// between muted-surface and mood-steady when active.
class DjToggle extends StatelessWidget {
  const DjToggle({
    super.key,
    required this.active,
    required this.onChanged,
    this.label = 'DJ',
  });

  final bool active;
  final ValueChanged<bool> onChanged;
  final String label;

  @override
  Widget build(BuildContext context) {
    final tween = active ? 1.0 : 0.0;
    return Semantics(
      button: true,
      toggled: active,
      label: 'Toggle DJ mode',
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () => onChanged(!active),
        child: TweenAnimationBuilder<double>(
          tween: Tween(begin: tween, end: tween),
          duration: VibeTokens.dMed,
          curve: Curves.easeOutCubic,
          builder: (context, t, _) {
            final bg = Color.lerp(
              VibeTokens.surfaceHi,
              VibeTokens.moodSteady.withValues(alpha: 0.85),
              t,
            )!;
            final fg = Color.lerp(
              VibeTokens.textSecondary,
              VibeTokens.textPrimary,
              t,
            )!;
            const width = 56.0;
            const height = 26.0;
            const dotSize = 18.0;
            final dotLeft = 4.0 + (width - dotSize - 8.0) * t;
            return Container(
              width: width,
              height: height,
              padding: const EdgeInsets.symmetric(horizontal: VibeTokens.s8),
              decoration: BoxDecoration(
                color: bg,
                borderRadius: BorderRadius.circular(VibeTokens.rLg),
                border: Border.all(color: VibeTokens.border),
              ),
              child: Stack(
                clipBehavior: Clip.none,
                children: [
                  Positioned(
                    left: dotLeft - VibeTokens.s8,
                    top: (height - dotSize) / 2 - 1,
                    child: Container(
                      width: dotSize,
                      height: dotSize,
                      decoration: BoxDecoration(
                        color: fg,
                        shape: BoxShape.circle,
                        boxShadow: t > 0.5
                            ? [
                                BoxShadow(
                                  color: VibeTokens.moodSteady
                                      .withValues(alpha: 0.4),
                                  blurRadius: 8,
                                ),
                              ]
                            : const [],
                      ),
                    ),
                  ),
                  Align(
                    alignment: t > 0.5
                        ? Alignment.centerLeft
                        : Alignment.centerRight,
                    child: Text(
                      label,
                      style: TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 0.5,
                        color: fg,
                      ),
                    ),
                  ),
                ],
              ),
            );
          },
        ),
      ),
    );
  }
}
