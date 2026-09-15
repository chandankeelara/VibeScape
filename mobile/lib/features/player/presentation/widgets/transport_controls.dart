import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:vibescape/core/theme/tokens.dart';

/// Prev / play / next row. Play button has a press-scale animation and
/// a mood-tinted ring. All buttons emit a light haptic on tap.
class TransportControls extends StatelessWidget {
  const TransportControls({
    super.key,
    required this.isPlaying,
    required this.onPlayPause,
    required this.onPrev,
    required this.onNext,
    required this.accentColor,
  });

  final bool isPlaying;
  final VoidCallback onPlayPause;
  final VoidCallback onPrev;
  final VoidCallback onNext;
  final Color accentColor;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        _GhostButton(
          icon: Icons.skip_previous_rounded,
          onTap: onPrev,
          semanticLabel: 'Previous',
        ),
        const SizedBox(width: VibeTokens.s24),
        _PlayButton(
          isPlaying: isPlaying,
          onTap: onPlayPause,
          accentColor: accentColor,
        ),
        const SizedBox(width: VibeTokens.s24),
        _GhostButton(
          icon: Icons.skip_next_rounded,
          onTap: onNext,
          semanticLabel: 'Next',
        ),
      ],
    );
  }
}

class _GhostButton extends StatefulWidget {
  const _GhostButton({
    required this.icon,
    required this.onTap,
    required this.semanticLabel,
  });

  final IconData icon;
  final VoidCallback onTap;
  final String semanticLabel;

  @override
  State<_GhostButton> createState() => _GhostButtonState();
}

class _GhostButtonState extends State<_GhostButton>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c;

  @override
  void initState() {
    super.initState();
    _c = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 120),
      lowerBound: 0,
      upperBound: 1,
    );
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  void _down(_) => _c.forward();
  void _up(_) => _c.reverse();

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: widget.semanticLabel,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapDown: _down,
        onTapUp: _up,
        onTapCancel: () => _c.reverse(),
        onTap: () {
          HapticFeedback.selectionClick();
          widget.onTap();
        },
        child: AnimatedBuilder(
          animation: _c,
          builder: (context, _) {
            final scale = 1.0 - 0.08 * _c.value;
            return Transform.scale(
              scale: scale,
              child: Container(
                width: 56,
                height: 56,
                decoration: BoxDecoration(
                  color: VibeTokens.surfaceHi.withValues(alpha: 0.6),
                  shape: BoxShape.circle,
                  border: Border.all(color: VibeTokens.border),
                ),
                child: Icon(
                  widget.icon,
                  color: VibeTokens.textPrimary,
                  size: 28,
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}

class _PlayButton extends StatefulWidget {
  const _PlayButton({
    required this.isPlaying,
    required this.onTap,
    required this.accentColor,
  });

  final bool isPlaying;
  final VoidCallback onTap;
  final Color accentColor;

  @override
  State<_PlayButton> createState() => _PlayButtonState();
}

class _PlayButtonState extends State<_PlayButton>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c;

  @override
  void initState() {
    super.initState();
    _c = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 140),
      lowerBound: 0,
      upperBound: 1,
    );
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: widget.isPlaying ? 'Pause' : 'Play',
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapDown: (_) => _c.forward(),
        onTapUp: (_) => _c.reverse(),
        onTapCancel: () => _c.reverse(),
        onTap: () {
          HapticFeedback.mediumImpact();
          widget.onTap();
        },
        child: AnimatedBuilder(
          animation: _c,
          builder: (context, _) {
            final scale = 1.0 - 0.10 * _c.value;
            return Transform.scale(
              scale: scale,
              child: AnimatedContainer(
                duration: VibeTokens.dMed,
                width: 76,
                height: 76,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: RadialGradient(
                    colors: [
                      widget.accentColor,
                      widget.accentColor.withValues(alpha: 0.85),
                    ],
                  ),
                  boxShadow: [
                    BoxShadow(
                      color: widget.accentColor.withValues(alpha: 0.45),
                      blurRadius: 24,
                      spreadRadius: -4,
                    ),
                  ],
                ),
                child: AnimatedSwitcher(
                  duration: VibeTokens.dFast,
                  transitionBuilder: (child, anim) => ScaleTransition(
                    scale: anim,
                    child: FadeTransition(opacity: anim, child: child),
                  ),
                  child: Icon(
                    widget.isPlaying ? Icons.pause_rounded : Icons.play_arrow_rounded,
                    key: ValueKey<bool>(widget.isPlaying),
                    color: Colors.white,
                    size: 40,
                  ),
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}
