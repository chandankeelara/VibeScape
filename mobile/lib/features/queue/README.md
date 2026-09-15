# queue

Play queue, DJ mode, and recommendations. Two-section UI: user-managed "Up next"
list (reorderable, dedupes trailing add) and "Recommended for this track"
(fetched from the backend content-based recs endpoint, debounced 250ms).

## Entry points

- `QueueSheet` — the container widget. Renders as a bottom
  `DraggableScrollableSheet` with spring-physics snap on narrow viewports and as
  a fixed side panel on tablets/desktop (≥900px width).
- `queueControllerProvider` — user queue state + mutators (add, remove, reorder,
  clear, advanceNext, jumpTo).
- `djControllerProvider` — DJ on/off + bounded taste buffers (plays, skips,
  adds).
- `recommendationsControllerProvider` — async list of recs for the current
  track. `requestFor(seedId)` debounces; `refreshNow(seedId)` fires immediately.

## API dependency

Controllers depend on `queueApiProvider` (from `application/queue_api.dart`).
It throws until overridden. The app-level bootstrap should forward it to
`vibescapeApiProvider` once Agent A publishes the recommendations method:

```dart
queueApiProvider.overrideWith((ref) {
  final api = ref.watch(vibescapeApiProvider);
  return _QueueApiAdapter(api);
})
```

## Not in pubspec

None. Uses only riverpod + cached_network_image which are already present.
