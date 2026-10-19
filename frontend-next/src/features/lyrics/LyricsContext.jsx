import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore,
} from 'react';
import { useQuery } from '@tanstack/react-query';
import * as api from '../../lib/api';
import { apiKey } from '../../lib/vibe';
import { usePlayer, usePlaybackClock } from '../../state/PlayerContext';
import {
  activeLineIndex, deriveView, offsetKey, playbackPath, seekFractionForLine,
  stepOffset, tapSyncOffset, youtubeDurationMatches,
} from './lyricsMath';
import { readLyricsOn, readOffset, writeLyricsOn, writeOffset } from './lyricsStore';

/**
 * Lyrics state for the player: on/off, the fetched lyrics, the panel state,
 * the per-recording offset, and the active line.
 *
 * THE ACTIVE LINE IS NOT IN THE CONTEXT VALUE. The playhead ticks ~4x a
 * second; putting the index in context would re-render every consumer on
 * every line. It lives in a tiny external store fed by usePlaybackClock, and
 * only the components that draw lines subscribe to it (useActiveLine), each
 * re-rendering only when the INDEX changes.
 *
 * The toggle never touches playback: it only decides whether lyrics are
 * fetched and shown.
 */

const LyricsCtx = createContext(null);

const DEFAULT_CREDIT = 'Lyrics from LRCLIB';

/** Ticks are 250ms apart; a line boundary between two is scheduled exactly. */
const BOUNDARY_LOOKAHEAD_MS = 400;
/** Never extrapolate further than this past the last tick (a stalled clock). */
const EXTRAPOLATE_MAX_MS = 1000;

function createActiveStore() {
  let value = -1;
  const subs = new Set();
  return {
    get: () => value,
    set(next) {
      if (next === value) return;
      value = next;
      subs.forEach((fn) => fn());
    },
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
  };
}

export function LyricsProvider({ children }) {
  const { current, mode, source, videoState, playing, seek } = usePlayer();

  const [on, setOn] = useState(readLyricsOn);
  // Written in an effect, not in the updater: StrictMode runs updaters twice.
  useEffect(() => { writeLyricsOn(on); }, [on]);
  const toggle = useCallback(() => setOn((v) => !v), []);
  const close = useCallback(() => setOn(false), []);

  /* ---------------------------------------------------------- the source */

  const path = playbackPath(mode, source);
  const youtubeId = path === 'youtube' && videoState === 'ready' ? current?.youtube_id || null : null;
  const sourceKey = offsetKey(path, current, youtubeId);
  const trackKey = current ? apiKey(current) : null;

  const query = useQuery({
    queryKey: ['lyrics', trackKey],
    queryFn: () => api.trackLyrics(trackKey),
    enabled: on && trackKey != null && trackKey !== '',
    // Lyrics do not change; an upstream failure should be asked again.
    staleTime: (q) => (q.state.data?.error ? 0 : Infinity),
    retry: (n, e) => e?.status !== 404 && e?.status !== 401 && n < 1,
  });
  const data = query.data;

  /* ------------------------------------------------- the clock */

  const [store] = useState(createActiveStore);
  const clockRef = useRef({ posMs: 0, durMs: 0, at: 0 });
  const live = useRef({ lines: null, offset: 0, playing: false, gateKey: null, matched: null });
  const boundaryTimer = useRef(0);

  const estimateMs = useCallback(() => {
    const c = clockRef.current;
    const drift = live.current.playing ? Math.min(performance.now() - c.at, EXTRAPOLATE_MAX_MS) : 0;
    return c.posMs + Math.max(0, drift);
  }, []);

  const recompute = useCallback(() => {
    const { lines, offset: off, playing: isPlaying } = live.current;
    clearTimeout(boundaryTimer.current);
    if (!lines) { store.set(-1); return; }
    const pos = estimateMs();
    const idx = activeLineIndex(lines, pos, off);
    store.set(idx);
    // The next boundary falls between two ticks: land on it, not up to
    // 250ms after it.
    const nextLine = lines[idx + 1];
    if (isPlaying && nextLine) {
      const dueIn = nextLine.t_ms + off - pos;
      if (dueIn > 0 && dueIn < BOUNDARY_LOOKAHEAD_MS) {
        boundaryTimer.current = setTimeout(recompute, dueIn + 5);
      }
    }
  }, [estimateMs, store]);

  /* ---------------------------------------------------------- the offset */

  // Session copies of what storage holds, so the offset works with storage
  // blocked. Undefined = not touched this session, read storage.
  const [offsets, setOffsets] = useState({});
  const stored = sourceKey ? readOffset(sourceKey) : null;
  const offset = sourceKey ? (offsets[sourceKey] ?? stored ?? 0) : 0;

  // `live` carries the offset the clock reads. Written here as well
  // as in the sync effect so a held ± button, which steps faster than React
  // commits, always steps from the newest value.
  const setOffset = useCallback((ms) => {
    if (!sourceKey) return;
    live.current.offset = ms;
    setOffsets((o) => ({ ...o, [sourceKey]: ms }));
    writeOffset(sourceKey, ms);
    recompute();
  }, [sourceKey, recompute]);

  /* ------------------------------------------------ the YouTube gate */

  // { key, match } so a verdict for one video can never leak onto the next.
  const [ytGate, setYtGate] = useState({ key: null, match: null });
  const ytMatch = ytGate.key === sourceKey ? ytGate.match : null;

  // "Sync manually" for this recording, this session. A stored offset means
  // it was synced before, which counts too.
  const [manualKey, setManualKey] = useState(null);
  const manualSync = !!sourceKey && (manualKey === sourceKey || offsets[sourceKey] != null || stored != null);
  const startManualSync = useCallback(() => setManualKey(sourceKey), [sourceKey]);

  const view = useMemo(
    () => deriveView({
      data,
      error: query.error,
      pending: query.isPending && query.fetchStatus !== 'idle',
      path,
      ytMatch,
      manualSync,
    }),
    [data, query.error, query.isPending, query.fetchStatus, path, ytMatch, manualSync],
  );
  const syncedLines = view.kind === 'synced' ? view.lines : null;

  /* ------------------------------------------------- the active line */

  useEffect(() => {
    live.current.lines = syncedLines;
    live.current.offset = offset;
    live.current.playing = playing;
    live.current.gateKey = path === 'youtube' ? sourceKey : null;
    live.current.matched = data?.matched_duration_ms ?? null;
    recompute();
  }, [syncedLines, offset, playing, path, sourceKey, data, recompute]);

  useEffect(() => () => clearTimeout(boundaryTimer.current), []);

  usePlaybackClock(({ position, duration }) => {
    clockRef.current = { posMs: position * 1000, durMs: duration * 1000, at: performance.now() };
    recompute();
    const { gateKey, matched } = live.current;
    if (gateKey) {
      const match = youtubeDurationMatches(duration * 1000, matched);
      setYtGate((g) => (g.key === gateKey && g.match === match ? g : { key: gateKey, match }));
    }
  });

  /* ------------------------------------------------------- the actions */

  const nudge = useCallback((delta) => setOffset(stepOffset(live.current.offset, delta)), [setOffset]);
  const reset = useCallback(() => setOffset(0), [setOffset]);
  const tapSync = useCallback(() => {
    if (!syncedLines) return;
    setOffset(tapSyncOffset(syncedLines, store.get(), estimateMs()));
  }, [syncedLines, setOffset, store, estimateMs]);

  // A 30s preview is not the song's timeline; only the full paths can seek
  // to a line.
  const canSeek = !!syncedLines && (path === 'spotify' || path === 'youtube');
  const seekToLine = useCallback((i) => {
    if (!canSeek || !syncedLines?.[i]) return;
    const frac = seekFractionForLine(syncedLines[i].t_ms, offset, clockRef.current.durMs);
    if (frac == null) return;
    // Show the clicked line at once rather than at the next tick.
    clockRef.current = { ...clockRef.current, posMs: frac * clockRef.current.durMs, at: performance.now() };
    recompute();
    seek(frac);
  }, [canSeek, syncedLines, offset, seek, recompute]);

  const { refetch } = query;
  const retry = useCallback(() => { refetch(); }, [refetch]);

  const value = useMemo(() => ({
    on, toggle, close,
    track: current,
    path,
    view,
    credit: data?.credit || DEFAULT_CREDIT,
    offset, nudge, reset, tapSync, canSync: !!syncedLines && !!sourceKey,
    startManualSync, canManualSync: path === 'youtube' && !!sourceKey,
    canSeek, seekToLine,
    retry,
    activeStore: store,
  }), [on, toggle, close, current, path, view, data, offset, nudge, reset, tapSync, syncedLines, sourceKey,
    startManualSync, canSeek, seekToLine, retry, store]);

  return <LyricsCtx.Provider value={value}>{children}</LyricsCtx.Provider>;
}

export function useLyrics() {
  const ctx = useContext(LyricsCtx);
  if (!ctx) throw new Error('useLyrics must be used inside <LyricsProvider>');
  return ctx;
}

/** The active line index (-1 before the first). Re-renders only when it changes. */
export function useActiveLine() {
  const { activeStore } = useLyrics();
  return useSyncExternalStore(activeStore.subscribe, activeStore.get, activeStore.get);
}
