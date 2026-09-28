/**
 * DJ mode React state: the toggle, the rolling event buffer, and the signal
 * collector that decides whether a track was completed or skipped.
 *
 * All the actual weighting lives in dj.js. This file's only job is knowing
 * *when* an event happened, which in the legacy app was done by hooking
 * advanceToNext() and loadTrack() directly. React owns those transitions now,
 * so instead we watch `current` change and attribute the outgoing track's
 * played ratio to it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { usePlayer, usePlaybackTime } from '../../state/PlayerContext';
import { useToast } from '../../state/ToastContext';
import { apiKey } from '../../lib/vibe';
import {
  appendEvent,
  bufferSignature,
  classifyTransition,
  loadEnabled,
  loadEvents,
  persistEnabled,
  persistEvents,
} from './dj';

/**
 * Samples playback position and hands the ratio to `onSample`.
 *
 * This renders null and exists purely to own the usePlaybackTime subscription,
 * which fires ~4x/sec. Mounting it as a leaf keeps that churn off the sidebar
 * — see the note at the top of PlayerContext.jsx.
 */
export function PlaybackRatioProbe({ onSample }) {
  const { position, duration } = usePlaybackTime();
  useEffect(() => {
    const ratio = duration > 0 && Number.isFinite(duration)
      ? Math.max(0, Math.min(1, position / duration))
      : 0;
    onSample(ratio);
  }, [position, duration, onSample]);
  return null;
}

export function useDj() {
  const { current, markSeen } = usePlayer();
  const toast = useToast();

  const [enabled, setEnabled] = useState(loadEnabled);
  const [events, setEvents] = useState(loadEvents);

  /**
   * Re-seed the seen-set from the restored buffer, once, on mount.
   *
   * The exclude list is the seen-set alone, and the seen-set is in-memory
   * while this buffer is in localStorage. Without this, a reload (or the PWA
   * relaunching) would make the last 100 tracks candidates again, seconds
   * after the user heard them. These events are by definition already-played
   * tracks, so marking them seen restores the session rather than extending
   * exclusion across genuinely new ones.
   */
  useEffect(() => {
    for (const e of events) markSeen({ id: e.track_id });
    // Mount only — `events` is the restored buffer; later appends mark
    // themselves seen through loadTrack.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const currentKey = apiKey(current);

  /** Latest played ratio, tagged with the track it belongs to. */
  const sampleRef = useRef({ key: null, ratio: 0 });
  const liveKeyRef = useRef(currentKey);
  const prevKeyRef = useRef(null);

  /**
   * Synchronous mirror of `events`.
   *
   * The autoplay picker has to flush the outgoing track's signal and then
   * immediately fetch with the updated buffer, in one tick — it cannot wait for
   * a re-render to see its own write. setEvents still drives rendering; this ref
   * is what the picker reads.
   */
  const eventsRef = useRef(events);

  /** Track whose transition has already been recorded, so it isn't counted twice. */
  const flushedRef = useRef(null);

  const onSample = useCallback((ratio) => {
    sampleRef.current = { key: liveKeyRef.current, ratio };
  }, []);

  const push = useCallback((evt) => {
    const next = appendEvent(eventsRef.current, evt);
    if (next === eventsRef.current) return next;
    eventsRef.current = next;
    persistEvents(next);
    setEvents(next);
    return next;
  }, []);

  /**
   * Record how the now-playing track ended, right now, and return the updated
   * buffer.
   *
   * Legacy called this at the top of advanceToNext() so the just-finished
   * track's signal was already in the buffer by the time the DJ fetched a
   * replacement. The autoplay picker calls it for the same reason; the effect
   * below then sees it was already flushed and skips.
   */
  const recordTransitionNow = useCallback(
    ({ natural = false } = {}) => {
      const key = liveKeyRef.current;
      if (!key || flushedRef.current === key) return eventsRef.current;
      const ratio = sampleRef.current.key === key ? sampleRef.current.ratio : 0;
      flushedRef.current = key;
      return push({
        track_id: key,
        action: classifyTransition(ratio, { natural }),
        played_ratio: ratio,
        ts: Date.now(),
      });
    },
    [push]
  );

  /**
   * Catch-all: record the outgoing track whenever a new one becomes current and
   * the picker didn't already do it (a next-click, a search result, a rec).
   *
   * The legacy app passed an explicit `natural` flag because it knew whether
   * the swap came from the 'ended' media event or a next-click. React drives
   * every swap through loadTrack(), so that distinction isn't observable here
   * and classification falls back entirely to the played ratio — which is what
   * the legacy code did anyway for everything except a true natural end.
   */
  useEffect(() => {
    const prev = prevKeyRef.current;
    if (prev && prev !== currentKey && flushedRef.current !== prev) {
      const ratio = sampleRef.current.key === prev ? sampleRef.current.ratio : 0;
      push({ track_id: prev, action: classifyTransition(ratio), played_ratio: ratio, ts: Date.now() });
    }
    prevKeyRef.current = currentKey || null;
    liveKeyRef.current = currentKey;
    if (flushedRef.current !== currentKey) flushedRef.current = null;
    if (sampleRef.current.key !== currentKey) sampleRef.current = { key: currentKey, ratio: 0 };
  }, [currentKey, push]);

  /** A manual queue-add is the strongest positive signal the user can give. */
  const recordQueued = useCallback(
    (t) => {
      const key = apiKey(t);
      if (key) push({ track_id: key, action: 'queued', played_ratio: null, ts: Date.now() });
    },
    [push]
  );

  const toggle = useCallback(() => {
    setEnabled((on) => {
      const next = !on;
      persistEnabled(next);
      toast(
        next ? 'DJ mode on — session-weighted picks in the sidebar.' : 'DJ mode off.',
        next ? 'success' : 'info'
      );
      return next;
    });
  }, [toast]);

  const signature = useMemo(() => bufferSignature(events), [events]);

  // Stable identity: the sidebar passes these straight into memoized rows, and
  // a fresh object every render would defeat that.
  return useMemo(
    () => ({ enabled, toggle, events, signature, recordQueued, recordTransitionNow, onSample }),
    [enabled, toggle, events, signature, recordQueued, recordTransitionNow, onSample]
  );
}
