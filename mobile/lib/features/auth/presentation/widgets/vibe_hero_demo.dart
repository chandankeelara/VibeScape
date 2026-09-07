import 'package:flutter/material.dart';
import 'package:vibescape/core/theme/tokens.dart';

/// Small live demo panel: shows a mock now-playing card whose mood + accent
/// color follow the vibe slider value. Mirrors the .np-card / .mood-ladder
/// in `frontend/login.html`.
class VibeHeroDemo extends StatefulWidget {
  const VibeHeroDemo({super.key});

  @override
  State<VibeHeroDemo> createState() => _VibeHeroDemoState();
}

class _VibeHeroDemoState extends State<VibeHeroDemo> {
  double _vibe = 42;

  static const _mockTracks = [
    (title: 'Nightscape', artist: 'Halogen', vibe: 8),
    (title: 'Long Drive', artist: 'Signal Loss', vibe: 28),
    (title: 'Late Night Drive', artist: 'The Midnight', vibe: 50),
    (title: 'Overdrive', artist: 'Kavinsky', vibe: 72),
    (title: 'Explode', artist: 'Volt', vibe: 92),
  ];

  @override
  Widget build(BuildContext context) {
    final mood = VibeTokens.moodFor(_vibe.round());
    final track = _mockTracks.reduce(
      (a, b) => (a.vibe - _vibe).abs() < (b.vibe - _vibe).abs() ? a : b,
    );
    return AnimatedContainer(
      duration: VibeTokens.dFast,
      padding: const EdgeInsets.all(VibeTokens.s20),
      decoration: BoxDecoration(
        color: VibeTokens.surface,
        borderRadius: BorderRadius.circular(VibeTokens.rLg),
        border: Border.all(color: VibeTokens.border),
        boxShadow: [
          BoxShadow(
            color: mood.color.withValues(alpha: 0.25),
            blurRadius: 40,
            spreadRadius: -10,
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 56,
                height: 56,
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    colors: [mood.color, mood.color.withValues(alpha: 0.4)],
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                  ),
                  borderRadius: BorderRadius.circular(VibeTokens.rMd),
                ),
              ),
              const SizedBox(width: VibeTokens.s12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      track.title,
                      style: const TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      track.artist,
                      style: const TextStyle(
                        color: VibeTokens.textSecondary,
                        fontSize: 13,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: VibeTokens.s16),
          Wrap(
            spacing: 6,
            children: [
              _chip(mood.label, mood.color),
              _chip('${_vibe.round()}/100', VibeTokens.textSecondary),
            ],
          ),
          const SizedBox(height: VibeTokens.s20),
          Text(
            '${_vibe.round()}',
            style: TextStyle(
              fontSize: 56,
              fontWeight: FontWeight.w700,
              color: mood.color,
              height: 1,
            ),
          ),
          Text(
            mood.label,
            style: TextStyle(
              fontSize: 14,
              color: mood.color,
              letterSpacing: 2,
            ),
          ),
          const SizedBox(height: VibeTokens.s16),
          SliderTheme(
            data: SliderTheme.of(context).copyWith(
              activeTrackColor: mood.color,
              thumbColor: mood.color,
              overlayColor: mood.color.withValues(alpha: 0.2),
              inactiveTrackColor: VibeTokens.border,
            ),
            child: Slider(
              value: _vibe,
              onChanged: (v) => setState(() => _vibe = v),
              min: 0,
              max: 100,
            ),
          ),
          const Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('sleep', style: TextStyle(color: VibeTokens.textMuted, fontSize: 11)),
              Text('chill', style: TextStyle(color: VibeTokens.textMuted, fontSize: 11)),
              Text('steady', style: TextStyle(color: VibeTokens.textMuted, fontSize: 11)),
              Text('hype', style: TextStyle(color: VibeTokens.textMuted, fontSize: 11)),
              Text('beast', style: TextStyle(color: VibeTokens.textMuted, fontSize: 11)),
            ],
          ),
        ],
      ),
    );
  }

  Widget _chip(String label, Color color) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.15),
          borderRadius: BorderRadius.circular(999),
          border: Border.all(color: color.withValues(alpha: 0.4)),
        ),
        child: Text(
          label,
          style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w500),
        ),
      );
}
