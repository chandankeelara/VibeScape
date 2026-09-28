/**
 * Play queue + recommendations sidebar.
 *
 * Ports frontend/app.js:4800-5079 (queue + recs) and index.html:478-512
 * (markup). The queue is user-managed only and never auto-fills: it grows from
 * search "+ queue" buttons and from this panel's recommendations. DJ mode
 * changes what the recommendations *are*, never what the queue contains.
 *
 * Under 641px this same panel becomes a slide-up sheet. The legacy version did
 * that with a hidden checkbox and a :checked sibling selector, purely because
 * vanilla JS had no state to hang it on; here it is ordinary React state, so
 * Escape-to-close and the focus move come for free.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { usePlayer } from '../../state/PlayerContext';
import { useToast } from '../../state/ToastContext';
import { apiKey, trackKey } from '../../lib/vibe';
import QueueRow from './QueueRow';
import { bufferSignature, fetchDjPicks } from './dj';
import { PlaybackRatioProbe, useDj } from './useDj';
import { RECS_LIMIT, useRecs } from './useRecs';
import { useQueueDrag } from './useQueueDrag';
import styles from './QueueSidebar.module.css';

const MOBILE_QUERY = '(max-width: 640px)';

function useIsMobile() {
  const [mobile, setMobile] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(MOBILE_QUERY).matches
  );
  useEffect(() => {
    const mq = window.matchMedia(MOBILE_QUERY);
    const onChange = (e) => setMobile(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return mobile;
}

export default function QueueSidebar() {
  const {
    queue, enqueue, enqueueAt, dequeueAt, clearQueue, reorderQueue,
    loadTrack, current, recent, setNextFallback, getSeenIds,
  } = usePlayer();
  const toast = useToast();
  const queryClient = useQueryClient();

  const {
    enabled: djEnabled, toggle: djToggle, events: djEvents,
    signature: djSignature, recordQueued, recordTransitionNow, onSample,
  } = useDj();
  const { recs, loading, hasAnchor } = useRecs({
    djEnabled, events: djEvents, signature: djSignature,
  });

  const isMobile = useIsMobile();
  const [sheetOpen, setSheetOpen] = useState(false);
  const [consumingKey, setConsumingKey] = useState(null);
  const sheetRef = useRef(null);
  const fabRef = useRef(null);

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
      loadTrack(track);
    },
    [queue.length, dequeueAt, loadTrack]
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
      loadTrack(track);
    },
    [djEnabled, flashConsume, loadTrack]
  );

  const addRec = useCallback(
    (track) => {
      if (inQueue(track)) {
        toast('Already in your queue.', 'info');
        return;
      }
      enqueue(track);
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
      enqueueAt(track, index);
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
    const sig = bufferSignature(events);
    // apiKey, not trackKey — useRecs builds its queryKey the same way.
    const interimKey = ['queue-recs', apiKey(current), 'dj', sig];

    // fetchQuery instead of a bare fetchDjPicks: an in-flight request under a
    // given key is shared, so useRecs attaches to this one rather than
    // starting its own. This is what keeps a song ending at ONE round-trip,
    // which is what the legacy lastFetchSig guard bought.
    const picks = await queryClient.fetchQuery({
      queryKey: interimKey,
      staleTime: 5000,
      queryFn: () => fetchDjPicks(current, { events, seen: getSeenIds(), limit: RECS_LIMIT }),
    });
    if (!picks?.length) return null;
    const [top, ...rest] = picks;
    flashConsume(trackKey(top));

    // Playing `top` changes the seed — another key that would otherwise
    // trigger its own POST. Both get the remainder, which is also what the
    // sidebar should show: the picks queued up behind the one now playing.
    queryClient.setQueryData(interimKey, rest);
    queryClient.setQueryData(['queue-recs', apiKey(top), 'dj', sig], rest);
    return top;
    // No `queue`/`recent` deps: the exclude list reads the seen-set instead,
    // so enqueueing no longer re-creates this callback and re-runs the
    // setNextFallback effect below on every queue mutation.
  }, [djEnabled, current, recordTransitionNow, flashConsume, queryClient, getSeenIds]);

  // Registered only while DJ is on; clearing it restores the random vibe pull.
  useEffect(() => {
    setNextFallback(djEnabled ? djPickNext : null);
    return () => setNextFallback(null);
  }, [djEnabled, djPickNext, setNextFallback]);

  const { listRef, emptyRef, dragging } = useQueueDrag({
    queue,
    recs,
    onReorder: reorderQueue,
    onInsert: insertAt,
    styles,
  });

  /* ------------------------------------------------------- mobile sheet */

  useEffect(() => {
    if (!sheetOpen) return;
    const onKey = (e) => { if (e.key === 'Escape') setSheetOpen(false); };
    document.addEventListener('keydown', onKey);
    sheetRef.current?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [sheetOpen]);

  // A sheet left open while the viewport grows would strand its state; close it
  // so the desktop sidebar is never in a half-dismissed mode.
  useEffect(() => { if (!isMobile) setSheetOpen(false); }, [isMobile]);

  const closeSheet = useCallback(() => {
    setSheetOpen(false);
    fabRef.current?.focus();
  }, []);

  const asSheet = isMobile && sheetOpen;
  const recsTitle = djEnabled ? 'DJ picks' : 'Recommended for this track';

  return (
    <>
      {/* Leaf subscriber: owns the ~4x/sec playback-time updates so the DJ can
          tell a completed track from a skipped one without re-rendering this
          sidebar on every tick. */}
      <PlaybackRatioProbe onSample={onSample} />

      {/* A closed sheet uses `inert`, not just aria-hidden, so it also drops
          out of the tab order — otherwise the first Tab on a phone lands
          inside an offscreen panel. */}

      <aside
        ref={sheetRef}
        tabIndex={-1}
        className={`${styles.sidebar} ${asSheet ? styles.sheetOpen : ''} ${dragging ? styles.dragActive : ''}`}
        aria-label="Play queue and recommendations"
        inert={isMobile && !sheetOpen}
      >
        <div className={styles.sheetHead} aria-hidden={!isMobile}>
          <span className={styles.grabber} />
          <span className={styles.sheetTitle}>Queue</span>
          <button type="button" className={styles.sheetClose} onClick={closeSheet} aria-label="Close queue sheet">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
                 strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <section className={styles.section}>
          <div className={styles.sectionHead}>
            <h2 className={styles.sectionTitle}>Up next</h2>
            {queue.length > 0 && <span className={styles.count}>{queue.length}</span>}
            {queue.length > 0 && (
              <button type="button" className={styles.clear} onClick={clearQueue} aria-label="Clear queue" title="Clear queue">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
                     strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <polyline points="3 6 5 6 21 6" />
                  <path d="M19 6l-2 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L5 6" />
                </svg>
                <span>clear</span>
              </button>
            )}
          </div>

          {queue.length === 0 ? (
            <div className={styles.empty} ref={emptyRef}>
              <p className={styles.emptyMsg}>Your queue is empty.</p>
              <p className={styles.emptyHint}>Add tracks from search or the recommendations below.</p>
            </div>
          ) : (
            <ol className={styles.list} ref={listRef}>
              {queue.map((t, i) => (
                <QueueRow
                  key={`${trackKey(t)}:${i}`}
                  track={t}
                  index={i}
                  kind="queue"
                  onActivate={jumpTo}
                  onAction={removeAt}
                  onMove={reorderQueue}
                />
              ))}
            </ol>
          )}
        </section>

        <section className={`${styles.section} ${djEnabled ? styles.djActive : ''}`}>
          <div className={styles.sectionHead}>
            <h2 className={styles.sectionTitle}>{recsTitle}</h2>
            <button
              type="button"
              className={`${styles.djToggle} ${djEnabled ? styles.djOn : ''}`}
              aria-pressed={djEnabled}
              onClick={djToggle}
              aria-label="Toggle DJ mode (auto-queue similar tracks)"
              title="DJ mode — picks shaped by what you play, skip, and add"
            >
              <span className={styles.djDot} aria-hidden="true" />
              <span>DJ</span>
            </button>
          </div>

          {loading ? (
            <div className={styles.recsState}>
              <span className={styles.spinner} aria-hidden="true" />
              <p className={styles.emptyHint}>Finding similar songs…</p>
            </div>
          ) : recs.length === 0 ? (
            <div className={styles.recsState}>
              <p className={styles.emptyHint}>
                {hasAnchor
                  ? 'No similar tracks in your library yet.'
                  : 'Play a track to see similar songs from your library.'}
              </p>
            </div>
          ) : (
            <ol className={styles.list}>
              {recs.map((t, i) => (
                <QueueRow
                  key={trackKey(t)}
                  track={t}
                  index={i}
                  kind="rec"
                  onActivate={playRec}
                  onAction={addRec}
                  consuming={consumingKey === trackKey(t)}
                />
              ))}
            </ol>
          )}
        </section>
      </aside>

      {/* Mobile-only affordances. Both are display:none above 640px. */}
      <button
        type="button"
        className={styles.scrim}
        onClick={closeSheet}
        tabIndex={-1}
        aria-hidden="true"
        data-open={asSheet ? 'true' : 'false'}
      />
      <button
        ref={fabRef}
        type="button"
        className={styles.fab}
        onClick={() => setSheetOpen((o) => !o)}
        aria-expanded={sheetOpen}
        aria-label={
          queue.length
            ? `Show queue and recommendations, ${queue.length} queued`
            : 'Show queue and recommendations'
        }
        title="Queue"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <line x1="8" y1="6" x2="21" y2="6" /><line x1="8" y1="12" x2="21" y2="12" />
          <line x1="8" y1="18" x2="21" y2="18" /><line x1="3" y1="6" x2="3.01" y2="6" />
          <line x1="3" y1="12" x2="3.01" y2="12" /><line x1="3" y1="18" x2="3.01" y2="18" />
        </svg>
        {queue.length > 0 && <span className={styles.fabCount} aria-hidden="true">{queue.length}</span>}
      </button>
    </>
  );
}
