import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/physics.dart';
import 'package:flutter/services.dart';
import 'package:vibescape/core/theme/tokens.dart';

/// Hero vibe slider. 0-100 with a mood-gradient track and spring physics on
/// thumb release. Uses [RawGestureDetector] with a
/// [HorizontalDragGestureRecognizer] rather than the default Material
/// [Slider] so we control the physics + haptics end-to-end.
class VibeSlider extends StatefulWidget {
  const VibeSlider({
    super.key,
    required this.value,
    required this.onChanged,
    required this.onChangeEnd,
    this.height = 56,
  });

  final int value; // authoritative value from parent (0-100)
  final ValueChanged<int> onChanged;
  final ValueChanged<int> onChangeEnd;
  final double height;

  @override
  State<VibeSlider> createState() => _VibeSliderState();
}

class _VibeSliderState extends State<VibeSlider>
    with SingleTickerProviderStateMixin {
  /// Animated thumb value (may lag slightly behind [widget.value] on release
  /// as the spring settles). Drives the visual thumb + fill.
  late final AnimationController _thumbCtrl;
  bool _dragging = false;
  double? _dragValue; // during drag, uncontrolled (0-100 double)
  int _lastHapticBucket = -1;
  double _lastWidth = 300;

  @override
  void initState() {
    super.initState();
    _thumbCtrl = AnimationController.unbounded(
      vsync: this,
      value: widget.value.toDouble(),
    );
    _lastHapticBucket = _bucketOf(widget.value);
  }

  @override
  void didUpdateWidget(covariant VibeSlider old) {
    super.didUpdateWidget(old);
    if (!_dragging && widget.value != old.value) {
      _thumbCtrl.value = widget.value.toDouble();
    }
  }

  @override
  void dispose() {
    _thumbCtrl.dispose();
    super.dispose();
  }

  int _bucketOf(int v) {
    if (v < 20) return 0;
    if (v < 40) return 1;
    if (v < 60) return 2;
    if (v < 80) return 3;
    return 4;
  }

  void _maybeHaptic(int v) {
    final b = _bucketOf(v);
    if (b != _lastHapticBucket) {
      _lastHapticBucket = b;
      HapticFeedback.selectionClick();
    }
  }

  double _xToValue(double dx) {
    final pct = (dx / _lastWidth).clamp(0.0, 1.0);
    return pct * 100.0;
  }

  void _onDragStart(DragStartDetails d) {
    _dragging = true;
    _thumbCtrl.stop();
    final v = _xToValue(d.localPosition.dx);
    _dragValue = v;
    _thumbCtrl.value = v;
    widget.onChanged(v.round());
    _maybeHaptic(v.round());
  }

  void _onDragUpdate(DragUpdateDetails d) {
    if (!_dragging) return;
    final current = _dragValue ?? widget.value.toDouble();
    final delta = d.primaryDelta ?? 0;
    final next = (current + (delta / _lastWidth) * 100.0).clamp(0.0, 100.0);
    _dragValue = next;
    // Thumb follows finger with a tiny lag — animate toward `next` fast.
    _thumbCtrl.animateTo(
      next,
      duration: const Duration(milliseconds: 60),
      curve: Curves.easeOutCubic,
    );
    final rounded = next.round();
    if (rounded != widget.value) {
      widget.onChanged(rounded);
      _maybeHaptic(rounded);
    }
  }

  void _onDragEnd(DragEndDetails d) {
    if (!_dragging) return;
    _dragging = false;
    final target = (_dragValue ?? widget.value.toDouble()).clamp(0.0, 100.0);
    _dragValue = null;
    // Spring settle toward the rounded value.
    final spring = SpringDescription.withDampingRatio(
      mass: 1,
      stiffness: 260,
      ratio: 0.85,
    );
    final velocity = (d.velocity.pixelsPerSecond.dx / _lastWidth) * 100.0;
    final sim = SpringSimulation(spring, _thumbCtrl.value, target, velocity);
    _thumbCtrl.animateWith(sim);
    widget.onChangeEnd(target.round());
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
                    ..onStart = _onDragStart
                    ..onUpdate = _onDragUpdate
                    ..onEnd = _onDragEnd;
                },
              ),
            },
            child: AnimatedBuilder(
              animation: _thumbCtrl,
              builder: (context, _) {
                final v = _thumbCtrl.value.clamp(0.0, 100.0);
                final pct = v / 100.0;
                final width = _lastWidth;
                final mood = VibeTokens.moodFor(v.round());
                return CustomPaint(
                  size: Size(width, widget.height),
                  painter: _VibeSliderPainter(
                    fillPct: pct,
                    thumbColor: mood.color,
                    dragging: _dragging,
                  ),
                );
              },
            ),
          ),
        );
      },
    );
  }
}

class _VibeSliderPainter extends CustomPainter {
  _VibeSliderPainter({
    required this.fillPct,
    required this.thumbColor,
    required this.dragging,
  });

  final double fillPct;
  final Color thumbColor;
  final bool dragging;

  static const _gradient = LinearGradient(
    colors: [
      VibeTokens.moodSleep,
      VibeTokens.moodChill,
      VibeTokens.moodSteady,
      VibeTokens.moodHype,
      VibeTokens.moodBeast,
    ],
  );

  @override
  void paint(Canvas canvas, Size size) {
    final trackHeight = 10.0;
    final trackY = size.height / 2 - trackHeight / 2;
    final trackRect = RRect.fromRectAndRadius(
      Rect.fromLTWH(0, trackY, size.width, trackHeight),
      const Radius.circular(999),
    );

    // Background gradient.
    final bgPaint = Paint()
      ..shader = _gradient.createShader(
        Rect.fromLTWH(0, trackY, size.width, trackHeight),
      )
      ..color = Colors.white.withValues(alpha: 0.28);
    canvas.saveLayer(trackRect.outerRect, Paint());
    canvas.drawRRect(trackRect, bgPaint);
    // Dim unfilled portion so filled area reads brighter.
    final dim = Paint()..color = Colors.black.withValues(alpha: 0.35);
    final unfilled = Rect.fromLTWH(
      size.width * fillPct,
      trackY,
      size.width * (1 - fillPct),
      trackHeight,
    );
    canvas.drawRect(unfilled, dim);
    canvas.restore();

    // Thumb.
    final thumbX = size.width * fillPct;
    final thumbY = size.height / 2;
    final baseR = 12.0;
    final r = dragging ? baseR * 1.35 : baseR;
    final glowPaint = Paint()
      ..color = thumbColor.withValues(alpha: dragging ? 0.55 : 0.35)
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 10);
    canvas.drawCircle(Offset(thumbX, thumbY), r + 6, glowPaint);
    final ringPaint = Paint()
      ..color = Colors.white
      ..style = PaintingStyle.fill;
    canvas.drawCircle(Offset(thumbX, thumbY), r, ringPaint);
    final corePaint = Paint()..color = thumbColor;
    canvas.drawCircle(Offset(thumbX, thumbY), r - 3, corePaint);
  }

  @override
  bool shouldRepaint(covariant _VibeSliderPainter old) =>
      old.fillPct != fillPct ||
      old.thumbColor != thumbColor ||
      old.dragging != dragging;
}
