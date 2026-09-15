import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/features/library/application/sync_controller.dart';
import 'package:vibescape/features/library/domain/sync_selection.dart';
import 'package:vibescape/features/library/presentation/widgets/playlist_url_input.dart';
import 'package:vibescape/features/library/presentation/widgets/sync_progress_view.dart';

/// Modal sheet with two tabs: sync-my-library and add-public-playlist.
/// Renders a progress view while a job is running.
class SyncModal extends ConsumerStatefulWidget {
  const SyncModal({super.key});

  static Future<void> show(BuildContext context) {
    return showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: VibeTokens.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(VibeTokens.rLg)),
      ),
      builder: (_) => const SyncModal(),
    );
  }

  @override
  ConsumerState<SyncModal> createState() => _SyncModalState();
}

class _SyncModalState extends ConsumerState<SyncModal>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 2, vsync: this);

  @override
  void initState() {
    super.initState();
    // Kick off playlist fetch as soon as the modal opens.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final s = ref.read(syncControllerProvider);
      if (s.phase == SyncPhase.idle) {
        ref.read(syncControllerProvider.notifier).loadPlaylists();
      }
    });
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(syncControllerProvider);
    return FractionallySizedBox(
      heightFactor: 0.9,
      child: SafeArea(
        top: false,
        child: Column(
          children: [
            const _Grabber(),
            _Header(state: state),
            TabBar(
              key: const Key('sync_tabs'),
              controller: _tabs,
              labelColor: VibeTokens.textPrimary,
              unselectedLabelColor: VibeTokens.textMuted,
              indicatorColor: VibeTokens.accent,
              tabs: const [
                Tab(text: 'Sync my library'),
                Tab(text: 'Add public playlist'),
              ],
            ),
            Expanded(
              child: TabBarView(
                controller: _tabs,
                children: const [
                  _LibraryTab(),
                  _UrlTab(),
                ],
              ),
            ),
          ],
        ),
      ),
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

class _Header extends StatelessWidget {
  const _Header({required this.state});
  final SyncControllerState state;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: VibeTokens.s16,
        vertical: VibeTokens.s8,
      ),
      child: Row(
        children: [
          const Expanded(
            child: Text(
              'Sync my Spotify library',
              style: TextStyle(
                color: VibeTokens.textPrimary,
                fontSize: 16,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
          IconButton(
            tooltip: 'Close',
            icon: const Icon(Icons.close, size: 16),
            color: VibeTokens.textMuted,
            onPressed: () => Navigator.of(context).maybePop(),
          ),
        ],
      ),
    );
  }
}

class _LibraryTab extends ConsumerWidget {
  const _LibraryTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(syncControllerProvider);
    final ctl = ref.read(syncControllerProvider.notifier);

    switch (state.phase) {
      case SyncPhase.idle:
      case SyncPhase.loadingPlaylists:
        return const Center(
          key: Key('sync_loading'),
          child: CircularProgressIndicator(),
        );
      case SyncPhase.running:
        return Padding(
          key: const Key('sync_running'),
          padding: const EdgeInsets.all(VibeTokens.s16),
          child: SyncProgressView(
            progress: state.progress ??
                const SyncProgress(
                  done: 0,
                  total: 0,
                  added: 0,
                  alreadyYours: 0,
                  queued: 0,
                ),
          ),
        );
      case SyncPhase.complete:
        return Padding(
          key: const Key('sync_complete'),
          padding: const EdgeInsets.all(VibeTokens.s16),
          child: Column(
            children: [
              const Icon(Icons.check_circle, color: VibeTokens.accent, size: 40),
              const SizedBox(height: VibeTokens.s8),
              const Text(
                'Library synced',
                style: TextStyle(
                  color: VibeTokens.textPrimary,
                  fontSize: 16,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: VibeTokens.s8),
              Text(
                'Added ${state.progress?.added ?? 0}, queued ${state.progress?.queued ?? 0}.',
                style: const TextStyle(color: VibeTokens.textMuted),
              ),
              const Spacer(),
              FilledButton(
                onPressed: () {
                  ctl.reset();
                  Navigator.of(context).maybePop();
                },
                child: const Text('Done'),
              ),
            ],
          ),
        );
      case SyncPhase.awaitingSelection:
        return _SelectionList(state: state, ctl: ctl);
    }
  }
}

class _SelectionList extends StatelessWidget {
  const _SelectionList({required this.state, required this.ctl});
  final SyncControllerState state;
  final SyncController ctl;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Expanded(
          child: ListView(
            key: const Key('sync_selection_list'),
            padding: const EdgeInsets.symmetric(horizontal: VibeTokens.s8),
            children: [
              CheckboxListTile(
                key: const Key('sync_liked'),
                value: state.selection.includeLiked,
                onChanged: (v) => ctl.setIncludeLiked(v ?? false),
                title: const Text('Liked Songs'),
                subtitle: const Text('Your saved tracks'),
                activeColor: VibeTokens.accent,
              ),
              CheckboxListTile(
                key: const Key('sync_top'),
                value: state.selection.includeTop,
                onChanged: (v) => ctl.setIncludeTop(v ?? false),
                title: const Text('Top Tracks'),
                subtitle: const Text('Your most-played on Spotify'),
                activeColor: VibeTokens.accent,
              ),
              const Divider(color: VibeTokens.border),
              const Padding(
                padding: EdgeInsets.symmetric(
                  horizontal: VibeTokens.s16,
                  vertical: VibeTokens.s8,
                ),
                child: Text(
                  'Your playlists',
                  style: TextStyle(
                    color: VibeTokens.textMuted,
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.5,
                  ),
                ),
              ),
              for (final p in state.playlists)
                CheckboxListTile(
                  key: ValueKey('pl-${p.id}'),
                  value: state.selection.playlistIds.contains(p.id),
                  onChanged: (v) => ctl.togglePlaylist(p.id, v ?? false),
                  title: Text(p.name),
                  subtitle: Text('${p.trackCount} tracks'),
                  activeColor: VibeTokens.accent,
                ),
            ],
          ),
        ),
        _Footer(state: state, ctl: ctl),
      ],
    );
  }
}

class _UrlTab extends ConsumerWidget {
  const _UrlTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(syncControllerProvider);
    final ctl = ref.read(syncControllerProvider.notifier);
    return Padding(
      padding: const EdgeInsets.all(VibeTokens.s16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text(
            'Paste a public Spotify playlist URL to import its tracks.',
            style: TextStyle(color: VibeTokens.textSecondary, fontSize: 12),
          ),
          const SizedBox(height: VibeTokens.s12),
          PlaylistUrlInput(
            initial: state.selection.publicPlaylistUrl,
            onChanged: ctl.setPublicPlaylistUrl,
          ),
          const Spacer(),
          _Footer(state: state, ctl: ctl),
        ],
      ),
    );
  }
}

class _Footer extends StatelessWidget {
  const _Footer({required this.state, required this.ctl});
  final SyncControllerState state;
  final SyncController ctl;

  @override
  Widget build(BuildContext context) {
    final busy = state.phase == SyncPhase.running;
    return Padding(
      padding: const EdgeInsets.all(VibeTokens.s16),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.end,
        children: [
          if (state.error != null)
            Expanded(
              child: Text(
                state.error!,
                style: TextStyle(
                  color: VibeTokens.danger.withValues(alpha: 0.9),
                  fontSize: 12,
                ),
              ),
            ),
          TextButton(
            onPressed: busy ? null : () => Navigator.of(context).maybePop(),
            child: const Text('Cancel'),
          ),
          const SizedBox(width: VibeTokens.s8),
          FilledButton(
            key: const Key('sync_start_btn'),
            style: FilledButton.styleFrom(
              backgroundColor: VibeTokens.accent,
            ),
            onPressed: busy || state.selection.isEmpty ? null : ctl.startSync,
            child: Text(busy ? 'Syncing…' : 'Sync'),
          ),
        ],
      ),
    );
  }
}
