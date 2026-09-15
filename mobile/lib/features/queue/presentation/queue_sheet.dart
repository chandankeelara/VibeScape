import 'package:flutter/material.dart';
import 'package:flutter/physics.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/features/queue/application/dj_controller.dart';
import 'package:vibescape/features/queue/application/queue_controller.dart';
import 'package:vibescape/features/queue/application/recommendations_controller.dart';
import 'package:vibescape/features/queue/domain/queue_track.dart';
import 'package:vibescape/features/queue/presentation/widgets/dj_toggle.dart';
import 'package:vibescape/features/queue/presentation/widgets/queue_item_tile.dart';
import 'package:vibescape/features/queue/presentation/widgets/recommendation_tile.dart';

/// Bottom sheet on phones, side panel on tablets/desktop. Contains two
/// sections: user-managed queue and recommendations.
class QueueSheet extends StatelessWidget {
  const QueueSheet({super.key});

  /// Wide-viewport breakpoint — matches the mobile-vs-desktop breakpoint the
  /// web app uses for `queue-sidebar` layout.
  static const double kDesktopBreakpoint = 900;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final wide = constraints.maxWidth >= kDesktopBreakpoint;
        if (wide) {
          return const _SidePanel();
        }
        return const _DraggableSheet();
      },
    );
  }
}

class _SidePanel extends StatelessWidget {
  const _SidePanel();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 340,
      decoration: const BoxDecoration(
        color: VibeTokens.surface,
        border: Border(left: BorderSide(color: VibeTokens.border)),
      ),
      child: const _QueueBody(),
    );
  }
}

/// Sheet with spring-physics dismiss. We can't use DraggableScrollableSheet
/// directly for the dismiss gesture (it uses linear snapping), so we wrap it
/// in a custom controller that drives snap animations with SpringSimulation.
class _DraggableSheet extends StatefulWidget {
  const _DraggableSheet();

  @override
  State<_DraggableSheet> createState() => _DraggableSheetState();
}

class _DraggableSheetState extends State<_DraggableSheet>
    with SingleTickerProviderStateMixin {
  final DraggableScrollableController _drag = DraggableScrollableController();
  late final AnimationController _spring;
  static const double _minSize = 0.15;
  static const double _initSize = 0.45;
  static const double _maxSize = 0.92;

  @override
  void initState() {
    super.initState();
    _spring = AnimationController.unbounded(vsync: this)
      ..addListener(() {
        if (!_drag.isAttached) return;
        _drag.jumpTo(_spring.value.clamp(_minSize, _maxSize));
      });
  }

  @override
  void dispose() {
    _spring.dispose();
    _drag.dispose();
    super.dispose();
  }

  void _snap(double from, double to) {
    _spring.stop();
    _spring.value = from;
    final sim = SpringSimulation(
      const SpringDescription(mass: 1, stiffness: 220, damping: 22),
      from,
      to,
      0,
    );
    _spring.animateWith(sim);
  }

  double _nearestSnap(double v) {
    const snaps = [_minSize, _initSize, _maxSize];
    return snaps.reduce(
      (a, b) => (v - a).abs() < (v - b).abs() ? a : b,
    );
  }

  @override
  Widget build(BuildContext context) {
    return DraggableScrollableSheet(
      controller: _drag,
      minChildSize: _minSize,
      initialChildSize: _initSize,
      maxChildSize: _maxSize,
      snap: false,
      builder: (context, scrollController) {
        return GestureDetector(
          onVerticalDragEnd: (details) {
            final current = _drag.isAttached ? _drag.size : _initSize;
            final target = _nearestSnap(current);
            _snap(current, target);
          },
          child: Container(
              decoration: const BoxDecoration(
                color: VibeTokens.surface,
                borderRadius: BorderRadius.only(
                  topLeft: Radius.circular(VibeTokens.rLg),
                  topRight: Radius.circular(VibeTokens.rLg),
                ),
                boxShadow: [
                  BoxShadow(color: Colors.black54, blurRadius: 24),
                ],
              ),
              child: Column(
                children: [
                  const _Grabber(),
                  Expanded(
                    child: _QueueBody(scrollController: scrollController),
                  ),
                ],
              ),
            ),
          );
        },
    );
  }
}

class _Grabber extends StatelessWidget {
  const _Grabber();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 44,
      height: 4,
      margin: const EdgeInsets.symmetric(vertical: VibeTokens.s8),
      decoration: BoxDecoration(
        color: VibeTokens.border,
        borderRadius: BorderRadius.circular(2),
      ),
    );
  }
}

class _QueueBody extends StatelessWidget {
  const _QueueBody({this.scrollController});
  final ScrollController? scrollController;

  @override
  Widget build(BuildContext context) {
    return CustomScrollView(
      controller: scrollController,
      slivers: const [
        SliverToBoxAdapter(child: _UpNextSection()),
        SliverToBoxAdapter(child: SizedBox(height: VibeTokens.s16)),
        SliverToBoxAdapter(child: _RecommendationsSection()),
        SliverToBoxAdapter(child: SizedBox(height: VibeTokens.s24)),
      ],
    );
  }
}

class _UpNextSection extends ConsumerWidget {
  const _UpNextSection();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final queue = ref.watch(queueControllerProvider);
    final controller = ref.read(queueControllerProvider.notifier);

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: VibeTokens.s12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Text(
                'Up next',
                style: TextStyle(
                  color: VibeTokens.textPrimary,
                  fontSize: 14,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.4,
                ),
              ),
              const SizedBox(width: VibeTokens.s8),
              if (queue.length > 0)
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: VibeTokens.s8,
                    vertical: 2,
                  ),
                  decoration: BoxDecoration(
                    color: VibeTokens.surfaceHi,
                    borderRadius: BorderRadius.circular(VibeTokens.rSm),
                  ),
                  child: Text(
                    '${queue.length}',
                    style: const TextStyle(
                      color: VibeTokens.textSecondary,
                      fontSize: 11,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              const Spacer(),
              if (queue.length > 0)
                TextButton.icon(
                  onPressed: controller.clear,
                  icon: const Icon(Icons.delete_outline, size: 14),
                  label: const Text('clear'),
                  style: TextButton.styleFrom(
                    foregroundColor: VibeTokens.textMuted,
                    padding: const EdgeInsets.symmetric(
                      horizontal: VibeTokens.s8,
                    ),
                  ),
                ),
            ],
          ),
          const SizedBox(height: VibeTokens.s8),
          if (queue.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: VibeTokens.s16),
              child: Text(
                'Your queue is empty.\nAdd tracks from search or the recommendations below.',
                style: TextStyle(
                  color: VibeTokens.textMuted,
                  fontSize: 12,
                ),
              ),
            )
          else
            // ReorderableListView needs a bounded height inside a
            // CustomScrollView; use shrinkWrap + never-scroll.
            ReorderableListView.builder(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              buildDefaultDragHandles: false,
              itemCount: queue.tracks.length,
              onReorder: controller.reorder,
              itemBuilder: (context, i) {
                final t = queue.tracks[i];
                return QueueItemTile(
                  key: ValueKey('q-${t.id}-$i'),
                  track: t,
                  index: i,
                  isCurrent: t.id == queue.currentId,
                  onTap: () => controller.jumpTo(i),
                  onRemove: () => controller.removeAt(i),
                );
              },
            ),
        ],
      ),
    );
  }
}

class _RecommendationsSection extends ConsumerWidget {
  const _RecommendationsSection();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(recommendationsControllerProvider);
    final dj = ref.watch(djControllerProvider);
    final queue = ref.read(queueControllerProvider.notifier);
    final djCtl = ref.read(djControllerProvider.notifier);
    final recsCtl = ref.read(recommendationsControllerProvider.notifier);

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: VibeTokens.s12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(
                child: Text(
                  'Recommended for this track',
                  style: TextStyle(
                    color: VibeTokens.textPrimary,
                    fontSize: 14,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.4,
                  ),
                ),
              ),
              DjToggle(
                active: dj.enabled,
                onChanged: (on) {
                  djCtl.setEnabled(on);
                  if (on && dj.lastSeedId != null) {
                    recsCtl.refreshNow(dj.lastSeedId!);
                  }
                },
              ),
            ],
          ),
          const SizedBox(height: VibeTokens.s8),
          async.when(
            data: (recs) {
              if (recs.isEmpty) {
                return const Padding(
                  padding: EdgeInsets.symmetric(vertical: VibeTokens.s16),
                  child: Text(
                    'Play a track to see similar songs from your library.',
                    style: TextStyle(
                      color: VibeTokens.textMuted,
                      fontSize: 12,
                    ),
                  ),
                );
              }
              return Column(
                children: [
                  for (final t in recs)
                    RecommendationTile(
                      key: ValueKey('rec-${t.id}'),
                      track: t,
                      onAddToQueue: () {
                        queue.add(t);
                        djCtl.recordQueued(t.id);
                      },
                    ),
                ],
              );
            },
            loading: () => const Padding(
              padding: EdgeInsets.symmetric(vertical: VibeTokens.s16),
              child: Row(
                children: [
                  SizedBox(
                    width: 14,
                    height: 14,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                  SizedBox(width: VibeTokens.s8),
                  Text(
                    'Finding similar songs…',
                    style: TextStyle(
                      color: VibeTokens.textMuted,
                      fontSize: 12,
                    ),
                  ),
                ],
              ),
            ),
            error: (e, _) => Padding(
              padding: const EdgeInsets.symmetric(vertical: VibeTokens.s16),
              child: Text(
                'Could not load recommendations.',
                style: TextStyle(
                  color: VibeTokens.danger.withValues(alpha: 0.9),
                  fontSize: 12,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
