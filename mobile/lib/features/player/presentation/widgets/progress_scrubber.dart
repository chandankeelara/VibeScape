import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/physics.dart';
import 'package:vibescape/core/theme/tokens.dart';

/// Draggable progress bar with a floating time chip that follows the thumb
/// during scrub. On release, the thumb settles via [SpringSimulation] and
/// [onSeek] is invoked with the final position (ms).
///
/// While the user is dragging, [positionMs] from the parent is ignored — the
/// widget shows the drag position instead. The parent controller should mark
/// itself `isScrubbing=true` so it stops applying live native-player updates.
class ProgressScrubber extends StatefulWidget {
  const ProgressScrubber({
    super.key,
    required this.positionMs,
    required this.durationMs,
    required this.accentColor,
    required this.onScrubStart,
    required this.onScrubUpdate,
    required this.onSeek,
    this.height = 40,
  });

  final int positionMs;
  final int durationMs;
  final Color accentColor;
  final VoidCallback onScrubStart;
  final ValueChanged<int> onScrubUpdate;
  final ValueChanged<int> onSeek;
  final double height;

  @override
  State<ProgressScrubber> createState() => _ProgressScrubberState();
}

class _ProgressScrubberState extends State<ProgressScrubber>
    with SingleTickerProviderStateMixin {
  late final AnimationController _thumb;
  bool _dragging = false;
  double? _dragValue; // ms
  double _lastWidth = 300;

  @override
  void initState() {
    super.initState();
    _thumb = AnimationController.unbounded(
      vsync: this,
      value: widget.positionMs.toDouble(),
    );
  }

  @override
  void didUpdateWidget(covariant ProgressScrubber old) {
    super.didUpdateWidget(old);
    if (!_dragging && widget.positionMs != old.positionMs) {
      _thumb.value = widget.positionMs.toDouble();
    }
  }

  @override
  void dispose() {
    _thumb.dispose();
    super.dispose();
  }

  double _xToMs(double dx) {
    final dur = widget.durationMs.clamp(1, 1 << 30).toDouble();
    final pct = (dx / _lastWidth).clamp(0.0, 1.0);
    return pct * dur;
  }

  void _onStart(DragStartDetails d) {
    _dragging = true;
    _thumb.stop();
    widget.onScrubStart();
    final ms = _xToMs(d.localPosition.dx);
    _dragValue = ms;
    _thumb.value = ms;
    widget.onScrubUpdate(ms.round());
  }

  void _onUpdate(DragUpdateDetails d) {
    if (!_dragging) return;
    final cur = _dragValue ?? widget.positionMs.toDouble();
    final dur = widget.durationMs.clamp(1, 1 << 30).toDouble();
    final delta = (d.primaryDelta ?? 0) / _lastWidth * dur;
    final next = (cur + delta).clamp(0.0, dur);
    _dragValue = next;
    _thumb.value = next;
    widget.onScrubUpdate(next.round());
  }

  void _onEnd(DragEndDetails d) {
    if (!_dragging) return;
    _dragging = false;
    final target = _dragValue ?? widget.positionMs.toDouble();
    _dragValue = null;
    final spring = SpringDescription.withDampingRatio(
      mass: 1,
      stiffness: 280,
      ratio: 0.9,
    );
    final dur = widget.durationMs.clamp(1, 1 << 30).toDouble();
    final velocity =
        (d.velocity.pixelsPerSecond.dx / _lastWidth) * dur;
    final sim = SpringSimulation(spring, _thumb.value, target, velocity);
    _thumb.animateWith(sim);
    widget.onSeek(target.round());
  }

  String _fmt(int ms) {
    final total = (ms / 1000).round();
    final m = total ~/ 60;
    final s = total % 60;
    return '$m:${s.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        _lastWidth = constraints.maxWidth;
        return SizedBox(
          height: widget.height,
          child: RawGestureDetector(
            behavior: HitTestBehavior.opaque,
            gestures: <Type, GestureRecognizerFactory>{
              HorizontalDragGestureRecognizer:
                  GestureRecognizerFactoryWithHandlers<
                      HorizontalDragGestureRecognizer>(
                () => HorizontalDragGestureRecognizer(),
                (r) {
                  r
                    ..onStart = _onStart
                    ..onUpdate = _onUpdate
                    ..onEnd = _onEnd;
                },
              ),
            },
            child: AnimatedBuilder(
              animation: _thumb,
              builder: (context, _) {
                final dur =
                    widget.durationMs.clamp(1, 1 << 30).toDouble();
                final pos = _thumb.value.clamp(0.0, dur);
                final pct = pos / dur;
                return Stack(
                  clipBehavior: Clip.none,
                  children: [
                    CustomPaint(
                      size: Size(_lastWidth, widget.height),
                      painter: _ScrubberPainter(
                        fillPct: pct,
                        accent: widget.accentColor,
                        dragging: _dragging,
                      ),
                    ),
                    if (_dragging)
                      Positioned(
                        left: (_lastWidth * pct) - 22,
                        top: -8,
                        child: _TimeChip(label: _fmt(pos.round())),
                      ),
                  ],
                );
              },
            ),
          ),
        );
      },
    );
  }
}

class _TimeChip extends StatelessWidget {
  const _TimeChip({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: VibeTokens.s8,
        vertical: VibeTokens.s2,
      ),
      decoration: BoxDecoration(
        color: VibeTokens.surfaceHi,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: VibeTokens.border),
      ),
      child: Text(
        label,
        style: const TextStyle(
          color: VibeTokens.textPrimary,
          fontSize: 11,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

class _ScrubberPainter extends CustomPainter {
  _ScrubberPainter({
    required this.fillPct,
    required this.accent,
    required this.dragging,
  });

  final double fillPct;
  final Color accent;
  final bool dragging;

  @override
  void paint(Canvas canvas, Size size) {
    final trackY = size.height / 2 - 3;
    final track = RRect.fromRectAndRadius(
      Rect.fromLTWH(0, trackY, size.width, 6),
      const Radius.circular(999),
    );
    canvas.drawRRect(track, Paint()..color = VibeTokens.border);
    final fillRect = Rect.fromLTWH(0, trackY, size.width * fillPct, 6);
    canvas.drawRRect(
      RRect.fromRectAndRadius(fillRect, const Radius.circular(999)),
      Paint()..color = accent,
    );
    final thumbX = size.width * fillPct;
    final r = dragging ? 8.0 : 6.0;
    canvas.drawCircle(
      Offset(thumbX, size.height / 2),
      r + 4,
      Paint()..color = accent.withValues(alpha: 0.25),
    );
    canvas.drawCircle(
      Offset(thumbX, size.height / 2),
      r,
      Paint()..color = Colors.white,
    );
  }

  @override
  bool shouldRepaint(covariant _ScrubberPainter old) =>
      old.fillPct != fillPct ||
      old.accent != accent ||
      old.dragging != dragging;
}
