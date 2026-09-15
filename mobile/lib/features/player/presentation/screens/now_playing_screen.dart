import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/features/player/application/player_controller.dart';
import 'package:vibescape/features/player/application/vibe_controller.dart';
import 'package:vibescape/features/player/domain/player_view_state.dart';
import 'package:vibescape/features/player/presentation/widgets/album_art_hero.dart';
import 'package:vibescape/features/player/presentation/widgets/mood_label.dart';
import 'package:vibescape/features/player/presentation/widgets/progress_scrubber.dart';
import 'package:vibescape/features/player/presentation/widgets/track_meta.dart';
import 'package:vibescape/features/player/presentation/widgets/transport_controls.dart';
import 'package:vibescape/features/player/presentation/widgets/vibe_slider.dart';

/// The hero now-playing screen. Composes art, meta, transport, progress, and
/// the vibe slider into the primary player surface.
class NowPlayingScreen extends ConsumerWidget {
  const NowPlayingScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(playerControllerProvider);
    final vibe = ref.watch(vibeControllerProvider);
    final mood = VibeTokens.moodFor(vibe);

    final state = async.valueOrNull ?? PlayerViewState.empty;
    final track = state.track;

    return Scaffold(
      backgroundColor: VibeTokens.bg,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: VibeTokens.s24,
            vertical: VibeTokens.s16,
          ),
          child: Column(
            children: [
              const SizedBox(height: VibeTokens.s16),
              Expanded(
                flex: 5,
                child: Center(
                  child: AlbumArtHero(
                    artworkUrl: track?.artworkUrl,
                    moodColor: mood.color,
                    isPlaying: state.isPlaying,
                  ),
                ),
              ),
              const SizedBox(height: VibeTokens.s24),
              TrackMeta(
                track: track,
                vibe: vibe,
                moodLabel: mood.label,
                moodColor: mood.color,
              ),
              const SizedBox(height: VibeTokens.s24),
              ProgressScrubber(
                positionMs: state.positionMs,
                durationMs: state.durationMs > 0
                    ? state.durationMs
                    : (track?.durationMs ?? 30000),
                accentColor: mood.color,
                onScrubStart: () =>
                    ref.read(playerControllerProvider.notifier).beginScrub(),
                onScrubUpdate: (ms) => ref
                    .read(playerControllerProvider.notifier)
                    .updateScrubPosition(ms),
                onSeek: (ms) =>
                    ref.read(playerControllerProvider.notifier).endScrub(ms),
              ),
              const SizedBox(height: VibeTokens.s8),
              _TimesRow(
                positionMs: state.positionMs,
                durationMs: state.durationMs > 0
                    ? state.durationMs
                    : (track?.durationMs ?? 30000),
              ),
              const SizedBox(height: VibeTokens.s16),
              TransportControls(
                isPlaying: state.isPlaying,
                accentColor: mood.color,
                onPlayPause: () => ref
                    .read(playerControllerProvider.notifier)
                    .togglePlayPause(),
                onPrev: () =>
                    ref.read(playerControllerProvider.notifier).previous(),
                onNext: () => ref
                    .read(playerControllerProvider.notifier)
                    .next(vibe: vibe),
              ),
              const SizedBox(height: VibeTokens.s24),
              Text(
                '$vibe',
                style: Theme.of(context).textTheme.displayMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: VibeTokens.textPrimary,
                    ),
              ),
              MoodLabel(vibe: vibe),
              const SizedBox(height: VibeTokens.s12),
              VibeSlider(
                value: vibe,
                onChanged: (v) =>
                    ref.read(vibeControllerProvider.notifier).setVibe(v),
                onChangeEnd: (v) {
                  ref.read(vibeControllerProvider.notifier).setVibe(v);
                  ref.read(vibeControllerProvider.notifier).commit();
                },
              ),
              const SizedBox(height: VibeTokens.s8),
              const _Ticks(),
              const SizedBox(height: VibeTokens.s16),
            ],
          ),
        ),
      ),
    );
  }
}

class _TimesRow extends StatelessWidget {
  const _TimesRow({required this.positionMs, required this.durationMs});
  final int positionMs;
  final int durationMs;

  String _fmt(int ms) {
    final total = (ms / 1000).round();
    final m = total ~/ 60;
    final s = total % 60;
    return '$m:${s.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final style = Theme.of(context)
        .textTheme
        .bodySmall
        ?.copyWith(color: VibeTokens.textMuted, fontFeatures: const []);
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(_fmt(positionMs), style: style),
        Text(_fmt(durationMs), style: style),
      ],
    );
  }
}

class _Ticks extends StatelessWidget {
  const _Ticks();

  @override
  Widget build(BuildContext context) {
    const labels = ['sleep', 'chill', 'steady', 'hype', 'beast'];
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        for (final l in labels)
          Text(
            l,
            style: const TextStyle(
              color: VibeTokens.textMuted,
              fontSize: 10,
              letterSpacing: 0.6,
            ),
          ),
      ],
    );
  }
}
