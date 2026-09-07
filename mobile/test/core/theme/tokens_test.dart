import 'package:flutter_test/flutter_test.dart';
import 'package:vibescape/core/theme/tokens.dart';

void main() {
  group('VibeTokens.moodFor', () {
    test('vibe < 20 → sleep', () {
      expect(VibeTokens.moodFor(0).label, 'sleep');
      expect(VibeTokens.moodFor(19).label, 'sleep');
    });
    test('20-39 → chill', () {
      expect(VibeTokens.moodFor(20).label, 'chill');
      expect(VibeTokens.moodFor(39).label, 'chill');
    });
    test('40-59 → steady', () {
      expect(VibeTokens.moodFor(50).label, 'steady');
    });
    test('60-79 → hype', () {
      expect(VibeTokens.moodFor(60).label, 'hype');
    });
    test('80+ → beast', () {
      expect(VibeTokens.moodFor(80).label, 'beast');
      expect(VibeTokens.moodFor(100).label, 'beast');
    });
  });
}
