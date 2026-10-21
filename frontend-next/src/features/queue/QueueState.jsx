/**
 * Queue + DJ state, lifted out of QueueSidebar so two surfaces can share it.
 *
 * S2 shows the queue twice on a phone: the home list behind the mini-player
 * ("Up next", "For this track") and the queue sheet. Both need the SAME DJ
 * state — and useDj must only ever be instantiated once ("importing useDj
 * anywhere else forks its state", frontend-next/CLAUDE.md). So the one
 * instance lives in this provider and every surface reads it from context.
 *
 * Everything here moved verbatim from QueueSidebar.jsx (2026-10-11); the
 * comments that explain the ordering of the DJ autoplay fetch came with it.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { usePlayer } from '../../state/PlayerContext';
import { useToast } from '../../state/ToastContext';
import { apiKey, trackKey } from '../../lib/vibe';
import { eventsSignature, fetchDjPicks } from './dj';
import { PlaybackRatioProbe, useDj } from './useDj';
import { RECS_LIMIT, recsQueryKey, useRecs } from './useRecs';

const QueueCtx = createContext(null);

export function QueueStateProvider({ children }) {
  const {
    queue, enqueue, enqueueAt, dequeueAt, loadTrack, current, setNextFallback, getSeenIds,
  } = usePlayer();
  const toast = useToast();
  const queryClient = useQueryClient();

  const {
    enabled: djEnabled, toggle: djToggle, discover, toggleDiscover, events: djEvents,
    signature: djSignature, recordQueued, recordPicked, recordTransitionNow, onSample,
  } = useDj();
  const { recs, loading, hasAnchor } = useRecs({
    djEnabled, discover, events: djEvents, signature: djSignature,
  });

  const [consumingKey, setConsumingKey] = useState(null);

  /* ------------------------------------------------------------ queue ops */

  const inQueue = useCallback(
    (t) => queue.some((x) => trackKey(x) === trackKey(t)),
    [queue]
  );

  /**
   * Jump the queue forward to `index`: everything above it is dropped and the
   * target plays now.
   *
   * Done as repeated dequeueAt(0) rather than one slice because dequeueAt is
   * the only removal the context exposes. It is safe to call in a loop because
   * it uses a functional setQueue, so each call removes the head of the
   * *then*-current list.
   */
  const jumpTo = useCallback(
    (track, index) => {
      if (index < 0 || index >= queue.length) return;
      for (let i = 0; i <= index; i++) dequeueAt(0);
      // Jumping the queue is a choice made now, on top of the earlier add.
      recordPicked(track);
      loadTrack(track, { source: 'queue', trigger: 'queue_jump' });
    },
    [queue.length, dequeueAt, loadTrack, recordPicked]
  );

  const removeAt = useCallback((_track, index) => dequeueAt(index), [dequeueAt]);

  /** Fade a DJ pick out before the list re-renders without it. Decorative only. */
  const flashConsume = useCallback((key) => {
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (!key || reduced) return;
    setConsumingKey(key);
    setTimeout(() => setConsumingKey(null), 420);
  }, []);

  const playRec = useCallback(
    (track) => {
      if (djEnabled) flashConsume(trackKey(track));
      // Logged before loadTrack, like a search: the outgoing track's verdict
      // lands after it, and the fetch for the new seed sees both.
      recordPicked(track);
      // 'pick', not 'dj': the DJ offered it, but the user chose it.
      loadTrack(track, { source: 'pick', trigger: 'pick' });
    },
    [djEnabled, flashConsume, loadTrack, recordPicked]
  );

  const addRec = useCallback(
    (track) => {
      if (inQueue(track)) {
        toast('Already in your queue.', 'info');
        return;
      }
      enqueue(track, { via: 'rec' });
      recordQueued(track);
      toast('Added to queue.', 'success');
    },
    [inQueue, enqueue, recordQueued, toast]
  );

  const insertAt = useCallback(
    (track, index) => {
      if (inQueue(track)) {
        toast('Already in your queue.', 'info');
        return;
      }
      enqueueAt(track, index, { via: 'drag' });
      recordQueued(track);
      toast('Added to queue.', 'success');
    },
    [inQueue, enqueueAt, recordQueued, toast]
  );

  /* ---------------------------------------------------------- DJ autoplay */

  /**
   * What plays when a track ends and the queue is empty, while DJ mode is on —
   * the port of djPickAndPlayFresh().
   *
   * Order matters and is the whole point: the outgoing track's signal is
   * flushed *first*, so the replacement is chosen by a taste vector that
   * already includes how the user treated the song that just finished. Then one
   * fetch, whose top result plays and whose remainder is what the sidebar
   * refreshes to. Returning null hands control back to PlayerContext, which
   * falls back to a random vibe pull rather than stalling.
   *
   * The queue is not touched. DJ mode never writes to it.
   */
  const djPickNext = useCallback(async () => {
    if (!djEnabled || !current) return null;
    // Deliberately not `natural: true`. PlayerContext routes both a finished
    // track and a next-click with an empty queue through next(), so this can't
    // tell them apart — and asserting "natural" would score a track the user
    // skipped 10 seconds in as a full listen. Let the played ratio decide,
    // which is exactly what advanceToNext() did.
    // Flushing the event calls setEvents SYNCHRONOUSLY, so React re-renders
    // during the await below with the new signature but the old `current`.
    // useRecs recomputes its key at that moment — so we must fetch through
    // React Query under exactly that key, or useRecs misses the cache and
    // fires a second POST for the vector we are already fetching.
    const events = recordTransitionNow({ natural: false });
    const sig = eventsSignature(events);
    // apiKey, not trackKey — useRecs builds its queryKey the same way.
    const interimKey = recsQueryKey({ seedKey: apiKey(current), djEnabled: true, discover, signature: sig });

    // fetchQuery instead of a bare fetchDjPicks: an in-flight request under a
    // given key is shared, so useRecs attaches to this one rather than
    // starting its own. This is what keeps a song ending at ONE round-trip,
    // which is what the legacy lastFetchSig guard bought.
    const picks = await queryClient.fetchQuery({
      queryKey: interimKey,
      staleTime: 5000,
      queryFn: () => fetchDjPicks(current, { events, seen: getSeenIds(), queue, limit: RECS_LIMIT, discover }),
    });
    if (!picks?.length) return null;
    const [top, ...rest] = picks;
    flashConsume(trackKey(top));

    // Playing `top` changes the seed — another key that would otherwise
    // trigger its own POST. Both get the remainder, which is also what the
    // sidebar should show: the picks queued up behind the one now playing.
    queryClient.setQueryData(interimKey, rest);
    queryClient.setQueryData(recsQueryKey({ seedKey: apiKey(top), djEnabled: true, discover, signature: sig }), rest);
    return top;
    // No `recent` dep: it is a strict subset of the seen-set, which is read
    // through a ref at fetch time. `queue` stays — it is unioned into the
    // exclude list, since a long-queued track can age out of the set.
  }, [djEnabled, discover, current, queue, recordTransitionNow, flashConsume, queryClient, getSeenIds]);

  // Registered only while DJ is on; clearing it restores the random vibe pull.
  // `{ discover }` is stamped on the play_start of every track the DJ serves,
  // so real listening can later be split by which pool it came from.
  useEffect(() => {
    setNextFallback(djEnabled ? djPickNext : null, djEnabled ? { discover } : null);
    return () => setNextFallback(null);
  }, [djEnabled, discover, djPickNext, setNextFallback]);

  const value = useMemo(() => ({
    djEnabled, djToggle, discover, toggleDiscover,
    recs, recsLoading: loading, hasAnchor,
    consumingKey,
    jumpTo, removeAt, playRec, addRec, insertAt,
  }), [djEnabled, djToggle, discover, toggleDiscover, recs, loading, hasAnchor, consumingKey,
    jumpTo, removeAt, playRec, addRec, insertAt]);

  return (
    <QueueCtx.Provider value={value}>
      {/* Leaf subscriber: owns the ~4x/sec playback-time updates so the DJ can
          tell a completed track from a skipped one without re-rendering any
          queue surface on every tick. */}
      <PlaybackRatioProbe onSample={onSample} />
      {children}
    </QueueCtx.Provider>
  );
}

export function useQueueState() {
  const ctx = useContext(QueueCtx);
  if (!ctx) throw new Error('useQueueState must be used inside <QueueStateProvider>');
  return ctx;
}
