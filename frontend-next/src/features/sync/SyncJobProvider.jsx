import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '../../lib/api';
import { usePlayer } from '../../state/PlayerContext';
import { useToast } from '../../state/ToastContext';

/**
 * Owns a running library import, ABOVE the modal that starts it.
 *
 * The job used to live inside SyncModal, which is lazily mounted and only
 * while open — so closing the window unmounted the poll and the job became
 * invisible even though the server kept working. That forced the modal to be
 * a blocking wait: the only way to watch an import was to sit in front of it.
 *
 * Lifting the job here is what makes importing a background task. The modal
 * becomes one view onto it, the pill another, and neither owns it.
 *
 * It also owns the two automatic behaviours, because both must happen whether
 * or not the modal is on screen:
 *
 *   - the first playable track starts playing by itself
 *   - `justBecamePlayable` fires once, so the UI can step out of the way
 *
 * Mounted inside PlayerProvider — it calls fetchForVibe() — and inside a
 * React Query provider for the poll.
 */

const SyncJobCtx = createContext(null);

/** Statuses the server will not move on from. */
const TERMINAL = new Set(['complete', 'error', 'cancelled']);

const ZERO = {
  total: 0,
  processed: 0,
  added_to_library: 0,
  already_in_library: 0,
  queued_for_analysis: 0,
  skipped: 0,
  // True while Spotify pages are still arriving, so `total` is a running
  // discovery count rather than a target.
  collecting: true,
};

export function SyncJobProvider({ children }) {
  const { fetchForVibe } = usePlayer();
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [jobId, setJobId] = useState(null);
  const [phase, setPhase] = useState('idle'); // idle | running | complete | error
  const [errorMsg, setErrorMsg] = useState('');
  const [summary, setSummary] = useState('');

  /*
   * One-shot latches. Both of these must fire exactly once per job, and both
   * are driven from a poll that re-runs every second, so a plain boolean in
   * state would re-trigger on the render between set and commit.
   */
  const notedRef = useRef(false);
  const autoplayedRef = useRef(false);
  const [justBecamePlayable, setJustBecamePlayable] = useState(false);

  const status = useQuery({
    queryKey: ['ingest-status', jobId],
    queryFn: () => api.ingestStatus(jobId),
    enabled: !!jobId,
    staleTime: 0,
    gcTime: 0,
    // Returning false on a terminal status is what stops the loop.
    refetchInterval: (q) => (TERMINAL.has(q.state.data?.status) ? false : 1000),
    // Keeps ticking while the tab is backgrounded, which is exactly when
    // someone has left an import running and gone elsewhere.
    refetchIntervalInBackground: true,
    retry: false,
  });

  const s = status.data;
  const progress = useMemo(() => ({ ...ZERO, ...(s || {}) }), [s]);

  /**
   * Tracks that can be played RIGHT NOW.
   *
   * Both buckets are already linked in user_tracks and already
   * ingestion_status='done' — the server front-loads them, so this climbs off
   * zero within a second or two of starting. `queued_for_analysis` is
   * deliberately excluded: those rows exist but have no preview or embedding
   * yet and would 404 the player.
   */
  const readyNow = progress.added_to_library + progress.already_in_library;

  /* ----------------------------------------------------------- the engine */

  useEffect(() => {
    if (!s) return;

    // A job-level note can arrive on the 202 or mid-flight (the silent follow
    // that unlocks playlist track access). Once per job.
    if (s.note && !notedRef.current) {
      notedRef.current = true;
      toast(s.note, 'info');
    }

    /*
     * The moment anything is playable: start playing it.
     *
     * This is the whole point of the rework — an import should not be a wait.
     * It runs here rather than in the modal so it happens even if the window
     * was never opened or has already been dismissed.
     */
    const ready = (s.added_to_library || 0) + (s.already_in_library || 0);
    if (ready > 0 && !autoplayedRef.current) {
      autoplayedRef.current = true;
      setJustBecamePlayable(true);
      // Programmatic: the library just became playable, the user did not skip
      // anything. 'replaced' keeps this out of the skip signal.
      fetchForVibe(undefined, { endReason: 'replaced' });
    }

    if (s.status === 'complete') {
      setSummary(completionSummary(s));
      setPhase('complete');
      // The library changed, so anything reporting membership is stale: the
      // search dropdown's in-library badges and any track list.
      queryClient.invalidateQueries({ queryKey: ['search'] });
      queryClient.invalidateQueries({ queryKey: ['tracks'] });
    } else if (s.status === 'error') {
      setErrorMsg(s.error_message || 'Sync failed.');
      setPhase('error');
    } else if (s.status === 'cancelled') {
      setPhase('idle');
      setJobId(null);
    }
  }, [s, toast, queryClient, fetchForVibe]);

  /* ---------------------------------------------------------- the handles */

  const begin = useCallback((id) => {
    notedRef.current = false;
    autoplayedRef.current = false;
    setJustBecamePlayable(false);
    setErrorMsg('');
    setSummary('');
    setJobId(id);
    setPhase('running');
  }, []);

  const fail = useCallback((message) => {
    setErrorMsg(message || 'Sync failed.');
    setPhase('error');
  }, []);

  /** The only thing that stops the server working. */
  const cancel = useCallback(() => {
    if (jobId) api.cancelIngest(jobId).catch(() => {});
    setJobId(null);
    setPhase('idle');
  }, [jobId]);

  /** Clear a finished job without stopping anything. */
  const dismiss = useCallback(() => {
    setJobId(null);
    setPhase('idle');
    setJustBecamePlayable(false);
  }, []);

  /** Consumed by whoever reacts to it, so it only ever fires once. */
  const clearPlayableFlag = useCallback(() => setJustBecamePlayable(false), []);

  const value = useMemo(
    () => ({
      jobId, phase, progress, readyNow, errorMsg, summary,
      collecting: progress.collecting,
      justBecamePlayable, clearPlayableFlag,
      begin, fail, cancel, dismiss,
      isRunning: phase === 'running',
    }),
    [jobId, phase, progress, readyNow, errorMsg, summary,
     justBecamePlayable, clearPlayableFlag, begin, fail, cancel, dismiss]
  );

  return <SyncJobCtx.Provider value={value}>{children}</SyncJobCtx.Provider>;
}

/**
 * Safe to call outside the provider — returns null rather than throwing.
 * The sync surfaces are lazily loaded and a missing provider should degrade
 * to "no import running", not white-screen the player.
 */
export function useSyncJob() {
  return useContext(SyncJobCtx);
}

/** Plain-language wrap-up. Mirrors what the progress view counts. */
export function completionSummary(s) {
  const ready = (s.added_to_library || 0) + (s.already_in_library || 0);
  const pending = s.queued_for_analysis || 0;
  if (!ready && !pending) return 'Nothing new to add.';
  const bits = [];
  if (ready) bits.push(`${ready} ready to play`);
  if (pending) bits.push(`${pending} still being analysed`);
  return bits.join(' · ');
}
