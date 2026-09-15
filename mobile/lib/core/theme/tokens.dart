import 'package:flutter/material.dart';

/// Design tokens mirrored from frontend/style.css so the mobile app reads as
/// the same product. If a token doesn't exist here, don't hard-code — add it.
class VibeTokens {
  VibeTokens._();

  // Base palette
  static const Color bg = Color(0xFF0B0B10);
  static const Color surface = Color(0xFF14141C);
  static const Color surfaceHi = Color(0xFF1E1E28);
  static const Color border = Color(0x1FFFFFFF);
  static const Color textPrimary = Color(0xFFF5F5F7);
  static const Color textSecondary = Color(0xB3F5F5F7);
  static const Color textMuted = Color(0x66F5F5F7);

  // Mood gradient (matches slider)
  static const Color moodSleep = Color(0xFF3B4B8C);
  static const Color moodChill = Color(0xFF4C6EF5);
  static const Color moodSteady = Color(0xFF12B886);
  static const Color moodHype = Color(0xFFFF8C42);
  static const Color moodBeast = Color(0xFFE03131);

  // Accents
  static const Color accent = Color(0xFF12B886);
  static const Color danger = Color(0xFFE03131);
  static const Color warning = Color(0xFFFFC542);

  // Spacing
  static const double s2 = 2;
  static const double s4 = 4;
  static const double s8 = 8;
  static const double s12 = 12;
  static const double s16 = 16;
  static const double s20 = 20;
  static const double s24 = 24;
  static const double s32 = 32;
  static const double s48 = 48;

  // Radii
  static const double rSm = 6;
  static const double rMd = 12;
  static const double rLg = 20;
  static const double rXl = 28;

  // Durations
  static const Duration dFast = Duration(milliseconds: 160);
  static const Duration dMed = Duration(milliseconds: 280);
  static const Duration dSlow = Duration(milliseconds: 480);

  /// Map a vibe score (0-100) to its mood label + accent color.
  static ({String label, Color color}) moodFor(int vibe) {
    if (vibe < 20) return (label: 'sleep', color: moodSleep);
    if (vibe < 40) return (label: 'chill', color: moodChill);
    if (vibe < 60) return (label: 'steady', color: moodSteady);
    if (vibe < 80) return (label: 'hype', color: moodHype);
    return (label: 'beast', color: moodBeast);
  }
}
