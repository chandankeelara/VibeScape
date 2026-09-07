import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/features/player/application/player_controller.dart';

/// Pure state for the vibe slider. Holds 0-100 int + debounces recommendation
/// fetches on release. Widgets drive it via [setVibe] during drag and
/// [commit] on gesture-release.
class VibeController extends Notifier<int> {
  Timer? _debounce;
  String? _lastMoodLabel;

  @override
  int build() {
    ref.onDispose(() => _debounce?.cancel());
    _lastMoodLabel = VibeTokens.moodFor(50).label;
    return 50;
  }

  /// Live update from drag gesture. No side-effects — cheap.
  void setVibe(int value) {
    final clamped = value.clamp(0, 100);
    if (clamped == state) return;
    state = clamped;
  }

  /// Returns true if [value] crosses into a new mood bucket vs the last one
  /// seen. Widgets use this to fire haptic ticks during drag.
  bool crossedThreshold(int value) {
    final label = VibeTokens.moodFor(value.clamp(0, 100)).label;
    if (label != _lastMoodLabel) {
      _lastMoodLabel = label;
      return true;
    }
    return false;
  }

  /// Called on gesture release. Debounces so a rapid re-drag cancels the
  /// pending fetch. After [debounce] elapses, requests a new track from the
  /// player controller.
  void commit({Duration debounce = const Duration(milliseconds: 350)}) {
    _debounce?.cancel();
    final target = state;
    _debounce = Timer(debounce, () async {
      await ref.read(playerControllerProvider.notifier).next(vibe: target);
    });
  }

  /// Cancel any pending debounced fetch — used when the widget is disposed
  /// mid-gesture or when a new drag begins.
  void cancelPending() {
    _debounce?.cancel();
  }
}

final vibeControllerProvider =
    NotifierProvider<VibeController, int>(VibeController.new);
