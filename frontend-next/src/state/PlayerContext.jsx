/**
 * PlayerContext — the ONLY bridge between React and the media layer.
 *
 * Everything imperative (the <audio> element, the YouTube iframe, the Spotify
 * SDK, the Web Audio analyser) lives in src/media/* as plain modules,
 * initialized once. This context exposes:
 *
 *   - low-frequency state React can usefully render (current track, playing,
 *     queue, vibe, mode)
 *   - imperative methods (play, pause, next, seek, …)
 *
 * It deliberately does NOT expose position/time — that updates several times a
 * second. Components that need it subscribe via usePlaybackTime(), which keeps
 * the high-frequency churn inside one small leaf component instead of
 * re-rendering the player tree.
 *
 * See src/media/README.md for why the boundary exists.
 */

import { createContext, useContext, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from '../lib/api';
import * as player from '../media/player';
import * as verifyMedia from '../media/verify';
import { applyAccent, canVerify, moodFor, trackKey, trackVibe } from '../lib/vibe';
import { emitMascot } from '../lib/mascotBus';
import * as listenLog from '../lib/listenLog';
import { useToast } from './ToastContext';

const PlayerCtx = createContext(null);

export const RECENT_MAX = 12;

/** A vibe that holds still this long is a settled change (telemetry only). */
const VIBE_SETTLE_MS = 1000;

/**
 * Session "seen" set — every track the user has interacted with, so the DJ
 * never recommends something already encountered this session.
 *
 * This is the ONLY source the DJ exclude list is built from — see
 * excludeIds() in features/queue/dj.js. `recent` (12, drives the trail UI)
 * and the DJ event log (50, replayed into the query vector) keep their own jobs
 * and their own sizes, but neither feeds exclusion any more: every track that
 * reaches either of them passed through markSeen first.
 *
 * Deliberately in-memory, not persisted: across sessions it would mean never
 * hearing a song twice, which is not the goal. useDj does re-seed it from the
 * last few hours of the restored event log on mount (DJ_SEEN_RESEED_HOURS),
 * so a mid-session reload doesn't resurrect what was just heard — that is
 * recovering this session, not persisting.
 *
 * Must equal DJ_MAX_EXCLUDES. The backend inlines these as SQL literals in a
 * NOT IN (...) clause and truncates at 200, so 200 is the ceiling.
 */
export const SEEN_MAX = 200;

export function PlayerProvider({ children }) {
  const toast = useToast();

  const [vibe, setVibeState] = useState(50);
  const [current, setCurrent] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [mode, setMode] = useState('audio'); // 'audio' | 'video'
  const [queue, setQueue] = useState([]);
  const queueRef = useRef([]);
  useEffect(() => { queueRef.current = queue; }, [queue]);
  const [recent, setRecent] = useState([]);
  const [source, setSource] = useState(null); // 'spotify' | 'preview' | null
  const [loadingTrack, setLoadingTrack] = useState(false);
  // 'idle' | 'loading' | 'ready' | 'unavailable' — drives the video stage UI.
  const [videoState, setVideoState] = useState('idle');

  // Guards against a slow request for an old vibe clobbering a newer one.
  const fetchToken = useRef(0);

  /*
   * Who last moved the vibe slider — 'user' | 'system' | null.
   *
   * There are exactly two writers of `vibe`: setVibe/shiftVibe (the user drags
   * it, or search snaps it on an explicit pick) and setVibeFromTrack (the app
   * sets it from whatever loaded). A user-set vibe is stated intent; a
   * system-set one is the recommender's own output echoed back, and logging
   * the two the same way would train /similar on itself.
   *
   * A ref, not state: this changes on every slider tick and must not
   * re-render. It starts null — the initial 50 is a default nobody chose, and
   * listenLog sends NOTHING for a provenance it does not know rather than
   * guessing, because a wrong label is worse than a missing one.
   */
  const vibeSourceRef = useRef(null);

  /* Live mirrors for the telemetry context provider. Refs so sampling them
     costs no render; they are written from effects, never read during one. */
  const vibeRef = useRef(50);
  /** Last vibe a vibe_change event reported, so a burst knows where it began. */
  const vibeLoggedRef = useRef(50);

  // Insertion-ordered so the cap drops the OLDEST. A ref, not state: this
  // changes on every play and must not re-render the tree, and the recs
  // queryFn reads it through getSeenIds() at fetch time anyway.
  const seenRef = useRef(new Map());

  // Optional override for what plays when a track ends with an empty queue.
  // DJ mode registers an async picker here so the just-finished track's signal
  // shapes the next choice. Deliberately a ref, not state: registering must not
  // re-render, and DJ must never write into the user's visible queue.
  const nextFallbackRef = useRef(null);

  // `next` changes identity whenever the queue does, but the media layer's
  // onEnded handler must be registered exactly once. Route through a ref so
  // the hook always calls the current `next` without re-registering.
  const nextRef = useRef(null);
  const prevRef = useRef(null);
  /* Lets prev() read the live trail without a side effect inside a state
     updater — see prev() below. */
  const recentRef = useRef([]);

  /* ------------------------------------------------------------ media sync */

  useEffect(() => {
    player.init();
    const unsubscribe = player.subscribe((s) => {
      setPlaying(s.playing);
      if (s.source !== undefined) setSource(s.source);
    });
    return () => {
      unsubscribe();
      // The media layer is a module singleton living OUTSIDE React, so
      // unmounting this provider drops the subscription but would otherwise
      // leave audio playing. That happens on sign-out, when AuthGate swaps
      // the whole app for the login card — legacy's signOutOfVibeScape tore
      // the player down explicitly and this is the equivalent.
      verifyMedia.stop();
      player.stop();
    };
  }, []);

  useEffect(() => { applyAccent(vibe); vibeRef.current = vibe; }, [vibe]);

  /*
   * Telemetry: one vibe_change per SETTLED change, not one per slider tick.
   * A drag is a burst of values; `from` is where the burst started and the
   * event goes out once the value has held still for VIBE_SETTLE_MS. The
   * envelope's vibe_source says who moved it (user, or the app following a
   * track). From an effect, not the setters: setVibeState runs inside updaters
   * and those must stay pure.
   */
  const vibeBurstRef = useRef({ from: null, timer: null });
  useEffect(() => {
    const b = vibeBurstRef.current;
    if (b.from === null) b.from = vibeLoggedRef.current;
    clearTimeout(b.timer);
    b.timer = setTimeout(() => {
      const from = b.from;
      b.from = null;
      if (from === vibe) return;
      vibeLoggedRef.current = vibe;
      listenLog.logEvent('vibe_change', { data: { from, to: vibe } });
    }, VIBE_SETTLE_MS);
    return () => clearTimeout(b.timer);
  }, [vibe]);

  /*
   * Hand the listening log a way to sample the things only React knows.
   *
   * Registering a getter rather than pushing values means telemetry reads the
   * live value at emit time and never causes a render. Re-running this in
   * StrictMode just reassigns the same closure — it is idempotent by
   * construction, which is why it is safe to do from an effect at all.
   *
   * dj_mode is read off nextFallbackRef because that IS DJ mode: QueueSidebar
   * registers the DJ picker when the toggle is on and clears it when off
   * (QueueSidebar.jsx:191). No new state, no new plumbing.
   */
  useEffect(() => {
    listenLog.setContextProvider(() => ({
      vibe: vibeRef.current,
      vibe_source: vibeSourceRef.current,
      dj_mode: !!nextFallbackRef.current,
    }));
  }, []);

  // Register media-layer callbacks ONCE. Without this, onEnded stays a no-op
  // and nothing advances when a track finishes — autoplay is dead app-wide.
  useEffect(() => {
    player.setHooks({
      onEnded: () => nextRef.current?.(),
      // Separate from onEnded on purpose: a lock-screen "next" is a SKIP, not
      // a completed listen, and DJ mode weights those very differently.
      onNext: (opts) => nextRef.current?.(opts),
      onPrevious: (opts) => prevRef.current?.(opts),
      onNeedsPremium: () =>
        toast('This track requires Spotify Premium to play (no preview available).', 'warning'),
      onVideoError: (info) => toast(info?.message || 'Video unavailable', 'warning'),
      onSpotifyError: (info) =>
        toast(info?.message || 'Spotify playback error', info?.kind === 'account' ? 'warning' : 'error'),
    });
  }, [toast]);

  /* ----------------------------------------------------------------- vibe */

  /** Record a track as encountered. Idempotent; re-marking refreshes recency. */
  const markSeen = useCallback((t) => {
    const n = Number(t?.id);
    if (!Number.isFinite(n)) return; // un-ingested Spotify result — no internal id
    const m = seenRef.current;
    m.delete(n); // re-insert so it moves to the end (most recent)
    m.set(n, true);
    if (m.size > SEEN_MAX) {
      const drop = m.size - SEEN_MAX;
      let i = 0;
      for (const k of m.keys()) {
        if (i++ >= drop) break;
        m.delete(k);
      }
    }
  }, []);

  /** Newest first, so a downstream cap keeps the most relevant. */
  const getSeenIds = useCallback(() => [...seenRef.current.keys()].reverse(), []);

  const setVibe = useCallback((v) => {
    // Stamped OUTSIDE the updater. Updater functions must be pure and
    // StrictMode invokes them twice; writing provenance inside one is the same
    // impurity that made prev() load every track twice (see prev() below).
    vibeSourceRef.current = 'user';
    setVibeState(Math.max(0, Math.min(100, Math.round(v))));
  }, []);

  const shiftVibe = useCallback((delta) => {
    vibeSourceRef.current = 'user';
    setVibeState((v) => Math.max(0, Math.min(100, v + delta)));
  }, []);

  /* --------------------------------------------------------------- tracks */

  const pushRecent = useCallback((t) => {
    if (!t) return;
    setRecent((prev) => {
      const id = trackKey(t);
      const next = prev.filter((r) => trackKey(r) !== id);
      next.push(t);
      return next.slice(-RECENT_MAX);
    });
  }, []);

  /**
   * Snap the global vibe to a track's own vibe, so the hero number, mood word,
   * tick highlight and accent colour all follow what's actually playing —
   * and so the next random pull is drawn from that song's band rather than
   * wherever the user last left the slider.
   *
   * Ported from legacy setVibeFromTrack (app.js:4210).
   */
  const setVibeFromTrack = useCallback((t) => {
    const v = trackVibe(t);
    // Bail before stamping: a track with no vibe leaves the slider — and so
    // its provenance — exactly as the last real writer left it.
    if (v == null) return;
    vibeSourceRef.current = 'system';
    setVibeState(v);
  }, []);

  /**
   * `source`, `endReason` and `trigger` are telemetry only — they change
   * nothing about playback. `source` defaults to 'pick' because every caller
   * that reaches here without naming one is an explicit pick off a list (a
   * rec, the recent trail); search, queue, DJ and autoplay name themselves.
   * Until 2026-10-10 the default was 'search', so older rows mix the two.
   * `endReason` defaults to 'skipped' inside the media layer — see the note on
   * player.loadTrack() for why that default is the correct one. `trigger` is
   * how the user ended the previous track.
   */
  const loadTrack = useCallback(
    (t, { syncVibe = true, source = 'pick', endReason, trigger } = {}) => {
      if (!t) return;
      setCurrent(t);
      pushRecent(t);
      markSeen(t);
      // Legacy syncs on explicit picks (search / queue / recs / DJ / trail)
      // but NOT on the random vibe fetch — that track is already inside the
      // requested band, so snapping would drift the slider on every skip.
      if (syncVibe) setVibeFromTrack(t);
      player.loadTrack(t, { mode, source, endReason, trigger });
    },
    [mode, pushRecent, setVibeFromTrack, markSeen]
  );

  /** Pull a random track in the current vibe band, excluding recents. */
  /**
   * `endReason` says why whatever is playing is about to be displaced.
   * 'skipped' is right for the mood slider and the arrow keys (a human cut the
   * track short); the post-library-sync re-roll passes 'replaced'.
   */
  const fetchForVibe = useCallback(
    async (v = vibe, { endReason = 'skipped', trigger = 'vibe_change' } = {}) => {
      const token = ++fetchToken.current;
      setLoadingTrack(true);
      try {
        const exclude = recent.map((r) => r.id).filter(Boolean);
        const t = await api.randomTrack({
          vibe: v,
          tolerance: 12,
          exclude_ids: exclude.length ? exclude.join(',') : undefined,
        });
        if (token !== fetchToken.current) return;
        loadTrack(t, { syncVibe: false, source: 'autoplay', endReason, trigger });
      } catch (e) {
        if (token !== fetchToken.current) return;
        if (e.status === 404) {
          setCurrent(null);
          player.stop();
        } else {
          toast('Could not load track. Check the backend.', 'error');
        }
      } finally {
        if (token === fetchToken.current) setLoadingTrack(false);
      }
    },
    [vibe, recent, loadTrack, toast]
  );


  /* --------------------------------------------------------------- verify */

  const [verifying, setVerifying] = useState(false);

  useEffect(() => verifyMedia.subscribe(({ active }) => {
    setVerifying(active);
    // Whoever stopped it — the 30s cap, the clip ending, or the user — main
    // playback comes back exactly where it was.
    if (!active) player.restoreAfterVerify();
  }), []);

  /** Play the 30s clip this track was classified from. */
  const startVerify = useCallback(async () => {
    if (!canVerify(current)) {
      toast('No classification audio for this track.', 'info');
      return;
    }
    player.snapshotAndPauseForVerify();
    const ok = await verifyMedia.start(current.preview_url);
    if (!ok) toast('Could not play classification audio — link may be broken.', 'error');
  }, [current, toast]);

  const stopVerify = useCallback(() => verifyMedia.stop(), []);

  // A new track always ends any verify session in progress.
  useEffect(() => { verifyMedia.stop(); }, [current]);

  /* ---------------------------------------------------------------- video */

  // Dedupe concurrent lookups for the same track (legacy video.lookupInFlight).
  const ytLookupRef = useRef(null);

  const resolveYoutubeId = useCallback(async (t) => {
    if (!t) return null;
    if (t.youtube_id) return t.youtube_id;
    // Already queried and came back empty — don't ask again.
    if (t.youtube_id === null && t.youtube_queried_at) return null;
    if (!t.id) return null;

    const inflight = ytLookupRef.current;
    if (inflight && inflight.trackId === t.id) return inflight.promise;

    const promise = (async () => {
      try {
        const body = await api.trackYoutube(t.id);
        const yid = body?.youtube_id || null;
        // Cache onto the track so a re-entry doesn't refetch.
        t.youtube_id = yid;
        t.youtube_queried_at = Date.now();
        return yid;
      } catch (e) {
        console.warn('[VibeScape] youtube lookup failed:', e);
        return null;
      }
    })();

    ytLookupRef.current = { trackId: t.id, promise };
    try {
      return await promise;
    } finally {
      if (ytLookupRef.current?.trackId === t.id) ytLookupRef.current = null;
    }
  }, []);

  /**
   * Resolve + cue whenever we're in video mode and the track changes. This is
   * the step player.setMode() deliberately does NOT do — the media module
   * knows how to cue an id, not how to find one.
   */
  useEffect(() => {
    if (mode !== 'video') { setVideoState('idle'); return; }
    if (!current) { setVideoState('unavailable'); return; }

    let cancelled = false;
    setVideoState('loading');

    resolveYoutubeId(current).then((yid) => {
      if (cancelled) return;
      if (!yid) { setVideoState('unavailable'); return; }
      setVideoState('ready');
      player.cueVideo(yid);
    });

    return () => { cancelled = true; };
  }, [mode, current, resolveYoutubeId]);

  /* ---------------------------------------------------------------- queue */

  /** Telemetry: a real add only — callers usually pre-check, this makes sure. */
  const logQueueAdd = useCallback((t, via) => {
    if (queueRef.current.some((x) => trackKey(x) === trackKey(t))) return;
    listenLog.logTrackEvent('queue_add', t, via ? { data: { via } } : {});
  }, []);

  const enqueue = useCallback((t, { via } = {}) => {
    markSeen(t);
    logQueueAdd(t, via);
    setQueue((q) => (q.some((x) => trackKey(x) === trackKey(t)) ? q : [...q, t]));
  }, [markSeen, logQueueAdd]);

  const dequeueAt = useCallback((i) => setQueue((q) => q.filter((_, idx) => idx !== i)), []);
  const clearQueue = useCallback(() => setQueue([]), []);

  /** Insert at a position — needed by drag-to-queue from search/recs. */
  const enqueueAt = useCallback((t, i, { via } = {}) => {
    markSeen(t);
    logQueueAdd(t, via);
    setQueue((q) => {
      if (q.some((x) => trackKey(x) === trackKey(t))) return q;
      const at = Math.max(0, Math.min(i, q.length));
      return [...q.slice(0, at), t, ...q.slice(at)];
    });
  }, [markSeen, logQueueAdd]);

  /** Move an item within the queue — drag-to-reorder. */
  const reorderQueue = useCallback((from, to) => {
    setQueue((q) => {
      if (from < 0 || from >= q.length) return q;
      let target = Math.max(0, Math.min(to, q.length));
      if (target === from || target === from + 1) return q;
      const next = q.slice();
      const [moved] = next.splice(from, 1);
      if (target > from) target -= 1;
      next.splice(target, 0, moved);
      return next;
    });
  }, []);

  /** Register an async () => track|null used instead of a random vibe pull. */
  const setNextFallback = useCallback((fn) => { nextFallbackRef.current = fn; }, []);

  /*
   * `trigger` defaults to the on-screen next button. Media keys pass their own
   * (setHooks above). When a track ended by itself this still runs, but the
   * play is already closed as 'completed', so the trigger is never used.
   * Guarded with typeof because a bare onClick={next} passes a click event.
   */
  const next = useCallback((opts) => {
    const trigger = typeof opts?.trigger === 'string' ? opts.trigger : 'next_button';
    if (queue.length) {
      const [head, ...rest] = queue;
      setQueue(rest);
      loadTrack(head, { source: 'queue', trigger });
      return;
    }
    const fallback = nextFallbackRef.current;
    if (fallback) {
      // The DJ fallback is a round trip to POST /similar, so it is the one
      // path where "next" can take a visible moment with nothing on screen
      // to say so. fetchForVibe() raises this flag itself; this branch never
      // did, which left the hero card sitting on the previous track's art.
      setLoadingTrack(true);
      Promise.resolve(fallback())
        .then((t) => (t ? loadTrack(t, { source: 'dj', trigger }) : fetchForVibe(undefined, { trigger })))
        .catch(() => fetchForVibe(undefined, { trigger }))
        // fetchForVibe() owns the flag once it takes over, and clears it in
        // its own finally — but it may also have bailed early on a stale
        // token, so clearing here too is what guarantees the overlay dies.
        .finally(() => setLoadingTrack(false));
      return;
    }
    fetchForVibe(undefined, { trigger });
  }, [queue, loadTrack, fetchForVibe]);

  /*
   * prev() used to do its work INSIDE the setRecent updater, so that it could
   * read fresh state. Updater functions must be pure: StrictMode calls them
   * twice in development to surface exactly this, which meant every press
   * scheduled loadTrack() twice — the track reloaded and restarted, and the
   * DJ buffer got a duplicate event.
   *
   * Reading `recent` through a ref instead keeps the state fresh without a
   * side effect in the updater, the same indirection nextRef already uses.
   */
  const prev = useCallback((opts) => {
    const trigger = typeof opts?.trigger === 'string' ? opts.trigger : 'prev';
    const r = recentRef.current;
    // On the first track of a session there is nowhere to go back to, so no
    // state changes and Bit must not act out a rewind that never happened.
    if (r.length < 2) return;
    emitMascot('rewind');
    const target = r[r.length - 2];
    // loadTrack re-pushes the target, so drop the tail first to avoid a
    // duplicate entry in the trail.
    setRecent(r.slice(0, -1));
    loadTrack(target, { source: 'pick', trigger });
  }, [loadTrack]);

  useEffect(() => { recentRef.current = recent; }, [recent]);
  useEffect(() => { nextRef.current = next; }, [next]);
  useEffect(() => { prevRef.current = prev; }, [prev]);

  /* -------------------------------------------------------------- control */

  const togglePlay = useCallback(() => {
    if (player.getState().playing) player.pause();
    else player.play();
  }, []);

  const seek = useCallback((frac) => player.seek(frac), []);

  const setPlaybackMode = useCallback((m) => {
    setMode(m);
    player.setMode(m, current);
  }, [current]);

  const value = useMemo(
    () => ({
      vibe, setVibe, shiftVibe, mood: moodFor(vibe).name,
      current, loadTrack, fetchForVibe, loadingTrack, setVibeFromTrack,
      playing, togglePlay, seek, next, prev, setNextFallback,
      mode, setPlaybackMode, videoState, resolveYoutubeId,
      verifying, startVerify, stopVerify, canVerify: canVerify(current),
      source,
      queue, enqueue, enqueueAt, dequeueAt, clearQueue, reorderQueue,
      markSeen, getSeenIds,
      recent,
    }),
    [vibe, setVibe, shiftVibe, current, loadTrack, fetchForVibe, loadingTrack, setVibeFromTrack,
     playing, togglePlay, seek, next, prev, setNextFallback, mode, setPlaybackMode, videoState, resolveYoutubeId, verifying, startVerify, stopVerify, source,
     queue, enqueue, enqueueAt, dequeueAt, clearQueue, reorderQueue, markSeen, getSeenIds, recent]
  );

  return <PlayerCtx.Provider value={value}>{children}</PlayerCtx.Provider>;
}

export function usePlayer() {
  const ctx = useContext(PlayerCtx);
  if (!ctx) throw new Error('usePlayer must be used inside <PlayerProvider>');
  return ctx;
}

/**
 * High-frequency playback position, isolated from the main context on purpose.
 * Only the progress bar and time labels should call this — it updates ~4×/sec
 * and would otherwise re-render the whole player tree.
 */
export function usePlaybackTime() {
  const [t, setT] = useState({ position: 0, duration: 0 });
  useEffect(() => player.subscribeTime(setT), []);
  return t;
}

/**
 * Verify countdown, isolated from the main context on purpose. Ticks ~4x/sec
 * while a clip plays, so only the overlay should subscribe.
 */
export function useVerifyCountdown() {
  const [state, setState] = useState({ active: false, remainingMs: 0 });
  useEffect(() => verifyMedia.subscribe(setState), []);
  return state;
}
