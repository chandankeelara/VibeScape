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
import { emitMascot } from '../../lib/mascotBus';
import { onDj } from '../../lib/djBus';
import {
  classifyTransition,
  loadEnabled,
  loadTracks,
  persistEnabled,
  persistTracks,
  tracksSignature,
  upsertTrack,
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
  const [tracks, setTracks] = useState(loadTracks);

  /**
   * Re-seed the seen-set from the restored verdict map, once, on mount.
   *
   * The exclude list is the seen-set alone, and the seen-set is in-memory
   * while this map is in localStorage. Without this, a reload (or the PWA
   * relaunching) would make the recently-touched tracks candidates again,
   * seconds after the user heard them. These are by definition already-touched
   * tracks, so marking them seen restores the session rather than extending
   * exclusion across genuinely new ones.
   */
  useEffect(() => {
    for (const e of Object.values(tracks)) markSeen({ id: e.id });
    // Mount only — later upserts mark themselves seen through loadTrack.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const currentKey = apiKey(current);

  /** Latest played ratio, tagged with the track it belongs to. */
  const sampleRef = useRef({ key: null, ratio: 0 });
  const liveKeyRef = useRef(currentKey);
  const prevKeyRef = useRef(null);

  /**
   * Synchronous mirror of `tracks`.
   *
   * The autoplay picker has to flush the outgoing track's signal and then
   * immediately fetch with the updated map, in one tick — it cannot wait for
   * a re-render to see its own write. setTracks still drives rendering; this
   * ref is what the picker reads.
   */
  const tracksRef = useRef(tracks);

  /** Track whose transition has already been recorded, so it isn't counted twice. */
  const flushedRef = useRef(null);

  const onSample = useCallback((ratio) => {
    sampleRef.current = { key: liveKeyRef.current, ratio };
  }, []);

  const push = useCallback((evt) => {
    const next = upsertTrack(tracksRef.current, evt);
    if (next === tracksRef.current) return next;
    tracksRef.current = next;
    persistTracks(next);
    setTracks(next);
    // Announced AFTER the map actually changed, so a verdictless event
    // (upsertTrack returns the same object when it carries no signal) cannot
    // make the mascot react to something that was never recorded.
    emitMascot(evt.action, evt);
    return next;
  }, []);

  /**
   * Record how the now-playing track ended, right now, and return the updated
   * buffer.
   *
   * Legacy called this at the top of advanceToNext() so the just-finished
   * track's signal was already in the map by the time the DJ fetched a
   * replacement. The autoplay picker calls it for the same reason; the effect
   * below then sees it was already flushed and skips.
   */
  const recordTransitionNow = useCallback(
    ({ natural = false } = {}) => {
      const key = liveKeyRef.current;
      if (!key || flushedRef.current === key) return tracksRef.current;
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

  /**
   * A search-and-play is explicit intent — "I want this specific vibe NOW."
   * Fires a positive signal immediately so the next DJ fetch biases toward
   * the picked song's embedding without waiting for playback outcome. The
   * subsequent completed/skip event ACCUMULATES on top (upsertTrack signed-
   * sums repeats), so a search-then-complete lands around +1.75 while a
   * search-then-early-skip nets to roughly zero — the honest "I wanted it
   * but it wasn't it" signal.
   */
  const recordSearched = useCallback(
    (t) => {
      const key = apiKey(t);
      if (key) push({ track_id: key, action: 'searched', played_ratio: null, ts: Date.now() });
    },
    [push]
  );

  /**
   * Pick up DJ-worthy events fired from outside the queue sidebar (search,
   * elsewhere) via the module-singleton bus. One subscription for the life of
   * this hook — the bus is in lib/ so the emitter never has to know who is
   * listening or whether anyone is at all.
   */
  useEffect(() => onDj((evt) => push(evt)), [push]);

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

  const signature = useMemo(() => tracksSignature(tracks), [tracks]);

  // Stable identity: the sidebar passes these straight into memoized rows, and
  // a fresh object every render would defeat that.
  return useMemo(
    () => ({ enabled, toggle, tracks, signature, recordQueued, recordSearched, recordTransitionNow, onSample }),
    [enabled, toggle, tracks, signature, recordQueued, recordSearched, recordTransitionNow, onSample]
  );
}
