import 'package:flutter/material.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/features/library/domain/sync_selection.dart';

/// Progress bar + counts. The bar tweens its fill smoothly between updates
/// rather than jumping, using [TweenAnimationBuilder].
class SyncProgressView extends StatelessWidget {
  const SyncProgressView({super.key, required this.progress});

  final SyncProgress progress;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TweenAnimationBuilder<double>(
          key: const Key('sync_progress_bar'),
          tween: Tween(begin: 0, end: progress.fraction),
          duration: VibeTokens.dMed,
          curve: Curves.easeOutCubic,
          builder: (context, value, _) {
            return ClipRRect(
              borderRadius: BorderRadius.circular(VibeTokens.rSm),
              child: Container(
                height: 6,
                color: VibeTokens.surfaceHi,
                child: FractionallySizedBox(
                  alignment: Alignment.centerLeft,
                  widthFactor: value,
                  child: Container(color: VibeTokens.accent),
                ),
              ),
            );
          },
        ),
        const SizedBox(height: VibeTokens.s8),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              '${(progress.fraction * 100).round()}%',
              style: const TextStyle(
                color: VibeTokens.textSecondary,
                fontSize: 12,
                fontWeight: FontWeight.w600,
              ),
            ),
            Text(
              '${progress.done} / ${progress.total}',
              style: const TextStyle(
                color: VibeTokens.textMuted,
                fontSize: 12,
              ),
            ),
          ],
        ),
        const SizedBox(height: VibeTokens.s4),
        Text(
          progress.currentTrack ?? 'Starting…',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            color: VibeTokens.textMuted,
            fontSize: 11,
            fontStyle: FontStyle.italic,
          ),
        ),
        const SizedBox(height: VibeTokens.s12),
        Row(
          children: [
            _Stat(label: 'added', value: progress.added),
            _Stat(label: 'already yours', value: progress.alreadyYours),
            _Stat(label: 'queued', value: progress.queued),
          ],
        ),
      ],
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value});
  final String label;
  final int value;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Column(
        children: [
          Text(
            label,
            style: const TextStyle(color: VibeTokens.textMuted, fontSize: 10),
          ),
          const SizedBox(height: 2),
          Text(
            '$value',
            style: const TextStyle(
              color: VibeTokens.textPrimary,
              fontSize: 18,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}
