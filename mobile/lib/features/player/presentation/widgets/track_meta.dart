import 'package:flutter/material.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/features/player/domain/player_view_state.dart';

/// Title / artist / chips block. Pure widget — parent supplies data.
class TrackMeta extends StatelessWidget {
  const TrackMeta({
    super.key,
    required this.track,
    required this.vibe,
    required this.moodLabel,
    required this.moodColor,
  });

  final PlayerTrack? track;
  final int vibe;
  final String moodLabel;
  final Color moodColor;

  @override
  Widget build(BuildContext context) {
    final t = track;
    final textTheme = Theme.of(context).textTheme;
    final title = t?.title ?? 'Move the slider to begin';
    final artist = t?.artist ?? '—';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.center,
      mainAxisSize: MainAxisSize.min,
      children: [
        AnimatedSwitcher(
          duration: VibeTokens.dMed,
          child: Text(
            title,
            key: ValueKey<String>('title-${t?.id ?? 'empty'}'),
            textAlign: TextAlign.center,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: textTheme.headlineSmall?.copyWith(
              fontWeight: FontWeight.w700,
              color: VibeTokens.textPrimary,
            ),
          ),
        ),
        const SizedBox(height: VibeTokens.s4),
        Text(
          t?.album == null || t!.album!.isEmpty
              ? artist
              : '$artist  ·  ${t.album}',
          textAlign: TextAlign.center,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: textTheme.bodyMedium?.copyWith(color: VibeTokens.textSecondary),
        ),
        const SizedBox(height: VibeTokens.s12),
        Wrap(
          spacing: VibeTokens.s8,
          runSpacing: VibeTokens.s8,
          alignment: WrapAlignment.center,
          children: [
            _Chip(label: moodLabel, color: moodColor),
            _Chip(label: 'vibe $vibe', color: moodColor.withValues(alpha: 0.7)),
            if (t?.genre != null && t!.genre!.isNotEmpty)
              _Chip(label: t.genre!, color: VibeTokens.moodChill),
            if (t?.source != null && t!.source!.isNotEmpty)
              _Chip(label: t.source!, color: VibeTokens.textMuted),
          ],
        ),
      ],
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip({required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: VibeTokens.dFast,
      padding: const EdgeInsets.symmetric(
        horizontal: VibeTokens.s12,
        vertical: VibeTokens.s4,
      ),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.16),
        border: Border.all(color: color.withValues(alpha: 0.35)),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: 12,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.4,
        ),
      ),
    );
  }
}
