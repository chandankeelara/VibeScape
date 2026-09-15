import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:vibescape/core/theme/tokens.dart';
import 'package:vibescape/features/library/application/search_controller.dart';
import 'package:vibescape/features/library/domain/search_result.dart';
import 'package:vibescape/features/library/presentation/widgets/search_result_tile.dart';

/// Text field + dropdown. Debounce lives in the controller — this widget
/// just forwards keystrokes and renders whatever async state the controller
/// exposes.
class LibrarySearchBar extends ConsumerStatefulWidget {
  const LibrarySearchBar({
    super.key,
    this.onResultTap,
    this.onAddToQueue,
  });

  final void Function(SearchResult)? onResultTap;
  final void Function(SearchResult)? onAddToQueue;

  @override
  ConsumerState<LibrarySearchBar> createState() => _LibrarySearchBarState();
}

class _LibrarySearchBarState extends ConsumerState<LibrarySearchBar> {
  final TextEditingController _text = TextEditingController();
  final FocusNode _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    _focus.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _text.dispose();
    _focus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(searchControllerProvider);
    final ctl = ref.read(searchControllerProvider.notifier);
    final showDropdown = _focus.hasFocus && _text.text.trim().isNotEmpty;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          decoration: BoxDecoration(
            color: VibeTokens.surfaceHi,
            borderRadius: BorderRadius.circular(VibeTokens.rMd),
            border: Border.all(color: VibeTokens.border),
          ),
          padding: const EdgeInsets.symmetric(horizontal: VibeTokens.s12),
          child: Row(
            children: [
              const Icon(Icons.search, size: 18, color: VibeTokens.textMuted),
              const SizedBox(width: VibeTokens.s8),
              Expanded(
                child: TextField(
                  controller: _text,
                  focusNode: _focus,
                  onChanged: ctl.setQuery,
                  onSubmitted: ctl.submit,
                  style: const TextStyle(color: VibeTokens.textPrimary),
                  decoration: const InputDecoration(
                    isCollapsed: true,
                    contentPadding: EdgeInsets.symmetric(vertical: 12),
                    border: InputBorder.none,
                    hintText: 'Search your library and Spotify…',
                    hintStyle: TextStyle(color: VibeTokens.textMuted),
                  ),
                ),
              ),
              if (_text.text.isNotEmpty)
                IconButton(
                  key: const Key('search_clear'),
                  tooltip: 'Clear',
                  icon: const Icon(Icons.close, size: 14),
                  color: VibeTokens.textMuted,
                  onPressed: () {
                    _text.clear();
                    ctl.clear();
                    setState(() {});
                  },
                ),
            ],
          ),
        ),
        if (showDropdown)
          AnimatedContainer(
            duration: VibeTokens.dFast,
            margin: const EdgeInsets.only(top: VibeTokens.s8),
            decoration: BoxDecoration(
              color: VibeTokens.surface,
              borderRadius: BorderRadius.circular(VibeTokens.rMd),
              border: Border.all(color: VibeTokens.border),
            ),
            child: _Dropdown(
              async: async,
              onTap: widget.onResultTap,
              onAdd: widget.onAddToQueue,
            ),
          ),
      ],
    );
  }
}

class _Dropdown extends StatelessWidget {
  const _Dropdown({required this.async, this.onTap, this.onAdd});

  final AsyncValue<List<SearchResult>> async;
  final void Function(SearchResult)? onTap;
  final void Function(SearchResult)? onAdd;

  @override
  Widget build(BuildContext context) {
    return async.when(
      data: (list) {
        if (list.isEmpty) {
          return const Padding(
            padding: EdgeInsets.all(VibeTokens.s16),
            child: Text(
              'No matches. Try a different query.',
              style: TextStyle(color: VibeTokens.textMuted, fontSize: 12),
            ),
          );
        }
        return ConstrainedBox(
          constraints: const BoxConstraints(maxHeight: 320),
          child: ListView.builder(
            key: const Key('search_results'),
            padding: EdgeInsets.zero,
            itemCount: list.length,
            itemBuilder: (context, i) {
              final r = list[i];
              return SearchResultTile(
                result: r,
                onTap: onTap == null ? null : () => onTap!(r),
                onAddToQueue: onAdd == null ? null : () => onAdd!(r),
              );
            },
          ),
        );
      },
      loading: () => const Padding(
        key: Key('search_loading'),
        padding: EdgeInsets.all(VibeTokens.s16),
        child: Row(
          children: [
            SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
            SizedBox(width: VibeTokens.s8),
            Text(
              'Searching…',
              style: TextStyle(color: VibeTokens.textMuted, fontSize: 12),
            ),
          ],
        ),
      ),
      error: (e, _) => Padding(
        padding: const EdgeInsets.all(VibeTokens.s16),
        child: Text(
          'Search failed.',
          style: TextStyle(
            color: VibeTokens.danger.withValues(alpha: 0.9),
            fontSize: 12,
          ),
        ),
      ),
    );
  }
}
