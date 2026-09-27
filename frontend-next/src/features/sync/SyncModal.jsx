/**
 * Library sync modal — port of frontend/app.js:5674-6250 + index.html:526-646.
 *
 * Two tabs:
 *   library — pick Liked Songs / Top Tracks / playlists, POST /api/ingest/spotify
 *   url     — paste a public playlist link, POST /api/ingest/spotify-public
 *
 * Both kick off a background job and then poll /api/ingest/status/<id>. The
 * legacy code hand-rolled a setInterval; here the poll is a React Query with
 * `refetchInterval`, which stops itself when the job reaches a terminal state
 * and is torn down automatically on unmount.
 *
 * The modal only needs a VibeScape session — NOT a Spotify one. The url tab
 * degrades to a sign-in prompt and the library tab to an explanatory error
 * when the Spotify token is absent (useSpotifyAuth().isConnected === false).
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import * as api from '../../lib/api';
import { useToast } from '../../state/ToastContext';
import { usePlayer } from '../../state/PlayerContext';
import { useSpotifyAuth } from '../../state/SpotifyAuthContext';
import { useFocusTrap } from './useFocusTrap';
import { isPlaylistLink, parsePlaylistId } from './playlist';
import styles from './SyncModal.module.css';

const TERMINAL = new Set(['complete', 'error', 'cancelled']);

const ZERO_PROGRESS = {
  processed: 0,
  total: 0,
  added_to_library: 0,
  already_in_library: 0,
  queued_for_analysis: 0,
  current_track: 'Starting…',
};

function SpotifyMark({ size = 24 }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor" aria-hidden="true">
      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.59 14.42a.62.62 0 0 1-.86.21c-2.36-1.44-5.33-1.77-8.83-.97a.62.62 0 1 1-.28-1.22c3.83-.87 7.13-.49 9.77 1.12.3.18.39.57.2.86zm1.23-2.74a.78.78 0 0 1-1.07.26c-2.7-1.66-6.82-2.14-10.02-1.17a.78.78 0 1 1-.45-1.5c3.66-1.1 8.2-.57 11.29 1.33.37.23.49.71.25 1.08zm.11-2.85c-3.24-1.92-8.59-2.1-11.68-1.16a.94.94 0 1 1-.54-1.8c3.55-1.07 9.45-.86 13.19 1.36a.94.94 0 0 1-.97 1.6z" />
    </svg>
  );
}

/** Three-bucket completion summary, worded exactly as the legacy app. */
function completionSummary(s) {
  const nAdded = s.added_to_library || 0;
  const nAlready = s.already_in_library || 0;
  const nQueued = s.queued_for_analysis || 0;
  const total = s.total || 0;
  const playable = nAdded + nAlready;
  let out =
    `${playable} of ${total} playable in your library now — ` +
    `${nAdded} added, ${nAlready} already yours`;
  if (nQueued > 0) {
    out +=
      `. ${nQueued} queued for analysis — they'll appear once the ` +
      'background worker finishes them.';
  } else {
    out += '.';
  }
  return out;
}

function SyncModalInner({ onClose, defaultTab }) {
  const toast = useToast();
  const { fetchForVibe } = usePlayer();
  const queryClient = useQueryClient();
  const { token, isConnected, signIn, signOut } = useSpotifyAuth();
  const signedIn = isConnected;

  // Smart default (legacy openSyncModal): Spotify-connected users land on the
  // library tab; everyone else on url, their only working option.
  const [tab, setTab] = useState(defaultTab || (signedIn ? 'library' : 'url'));
  const [phase, setPhase] = useState('pick'); // pick | progress | complete | error
  const [errorMsg, setErrorMsg] = useState('');
  const [jobId, setJobId] = useState(null);
  const [noteShown, setNoteShown] = useState(false);
  const [summary, setSummary] = useState('');

  const [liked, setLiked] = useState(true);
  const [top, setTop] = useState(false);
  const [picked, setPicked] = useState(() => new Set());

  const [url, setUrl] = useState('');
  const [urlError, setUrlError] = useState('');

  const close = useCallback(() => {
    // Legacy cancels the running job on any dismissal path (X, backdrop,
    // Cancel, Escape). Fire-and-forget — the backend cleans up regardless.
    if (phase === 'progress' && jobId) api.cancelIngest(jobId).catch(() => {});
    onClose?.();
  }, [phase, jobId, onClose]);

  const cardRef = useFocusTrap(true, close);

  /* --------------------------------------------------------------- library */

  const library = useQuery({
    queryKey: ['spotify-library'],
    queryFn: () => api.spotifyLibrary({}, token),
    enabled: tab === 'library' && signedIn && phase === 'pick',
    staleTime: 60_000,
  });

  const playlists = useMemo(
    () => (Array.isArray(library.data?.playlists) ? library.data.playlists : []),
    [library.data]
  );

  const selection = useMemo(() => {
    const lib = library.data || {};
    const ids = [...picked];
    let total = 0;
    if (liked) total += lib.liked_count || 0;
    if (top) total += lib.top_tracks_count || 0;
    for (const p of playlists) if (picked.has(p.id)) total += p.track_count || 0;
    return { liked, top, playlist_ids: ids, total, any: liked || top || ids.length > 0 };
  }, [liked, top, picked, playlists, library.data]);

  const togglePlaylist = (id) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  /* ------------------------------------------------------------------- url */

  const parsedId = useMemo(() => parsePlaylistId(url), [url]);
  const urlValid = !!parsedId;
  const urlState = !url.trim() ? 'empty' : urlValid ? 'valid' : 'invalid';
  const urlHint = urlError
    ? urlError
    : urlState === 'empty'
      ? 'Paste a playlist link to continue'
      : urlState === 'valid'
        ? `Looks good — playlist ${parsedId}`
        : 'Not a Spotify playlist URL';

  /* -------------------------------------------------------------- polling */

  const status = useQuery({
    queryKey: ['ingest-status', jobId],
    queryFn: () => api.ingestStatus(jobId),
    enabled: !!jobId,
    staleTime: 0,
    gcTime: 0,
    // Replaces the legacy setInterval(pollSyncStatus, 1000). Returning false
    // on a terminal status is what stops the loop.
    refetchInterval: (q) => (TERMINAL.has(q.state.data?.status) ? false : 1000),
    refetchIntervalInBackground: true,
    retry: false,
  });

  const s = status.data;

  useEffect(() => {
    if (!s) return;
    // A job-level note can arrive on the 202 OR mid-flight here (the silent
    // follow that unlocks playlist track access). Show it once per job.
    if (s.note && !noteShown) {
      toast(s.note, 'info');
      setNoteShown(true);
    }
    if (s.status === 'complete') {
      setSummary(completionSummary(s));
      setPhase('complete');
      // An ingest changes what's in the user's library, so anything that
      // reports library membership is now stale: the search dropdown's
      // in-library badges (port-search caches under ['search']) and any
      // track list.
      queryClient.invalidateQueries({ queryKey: ['search'] });
      queryClient.invalidateQueries({ queryKey: ['tracks'] });
    } else if (s.status === 'error') {
      setErrorMsg(s.error_message || 'Sync failed.');
      setPhase('error');
    }
  }, [s, noteShown, toast, queryClient]);

  const progress = phase === 'progress' ? { ...ZERO_PROGRESS, ...(s || {}) } : ZERO_PROGRESS;
  const pct =
    progress.total > 0
      ? Math.min(100, Math.round(((progress.processed || 0) / progress.total) * 100))
      : 0;

  /* ------------------------------------------------------------- mutations */

  const startLibrary = useMutation({
    mutationFn: () =>
      api.ingestSpotify(
        {
          access_token: token,
          sources: {
            liked: selection.liked,
            top_tracks: selection.top,
            playlist_ids: selection.playlist_ids,
          },
        },
        token
      ),
    onMutate: () => {
      setNoteShown(false);
      setPhase('progress');
    },
    onSuccess: (j) => {
      if (!j?.job_id) {
        setErrorMsg('Could not start sync. Try again.');
        setPhase('error');
        return;
      }
      if (j.note) {
        toast(j.note, 'info');
        setNoteShown(true);
      }
      setJobId(j.job_id);
    },
    onError: () => {
      setErrorMsg('Could not start sync. Try again.');
      setPhase('error');
    },
  });

  const startPublic = useMutation({
    mutationFn: () => {
      // Backend accepts either field; legacy sends both when it has a real
      // link. The Spotify token is forwarded when present — Spotify killed
      // app-level public-playlist reads in Nov 2024, so without a user token
      // the backend will 403 on most real playlists, which is honest.
      const body = { playlist_id: parsedId };
      if (isPlaylistLink(url)) body.playlist_url = url.trim();
      if (token) body.access_token = token;
      return api.ingestSpotifyPublic(body, token || undefined);
    },
    onMutate: () => {
      setUrlError('');
      setNoteShown(false);
      setPhase('progress');
    },
    onSuccess: (j) => {
      if (!j?.job_id) {
        toast('Could not add playlist. Try again.', 'error');
        setPhase('pick');
        return;
      }
      if (j.note) {
        toast(j.note, 'info');
        setNoteShown(true);
      }
      setJobId(j.job_id);
    },
    onError: (e) => {
      // Domain-specific codes per the backend contract. All of them drop back
      // to the url tab rather than the generic error view — the user can fix
      // the link and retry in place.
      setPhase('pick');
      setTab('url');
      const code = e?.body?.detail?.error || e?.body?.error || '';
      if (e?.status === 400) {
        setUrlError('Invalid playlist URL. Double-check the link.');
        return;
      }
      if (e?.status === 401) {
        signOut();
        const msg =
          code === 'spotify_scope_upgrade_required'
            ? 'Please sign in with Spotify again to grant the new permission (needed to add playlists by link).'
            : 'Your Spotify session expired. Sign in with Spotify again.';
        toast(msg, 'error', { action: { label: 'Sign in', onClick: () => signIn() } });
        return;
      }
      if (e?.status === 404) {
        toast('Playlist not found. Is the ID correct?', 'error');
        return;
      }
      if (e?.status === 403) {
        toast("This playlist isn't public. Only public playlists can be added this way.", 'error');
        return;
      }
      toast('Could not add playlist. Try again.', 'error');
    },
  });

  /* ---------------------------------------------------------------- footer */

  const onPrimary = () => {
    if (phase === 'complete') {
      onClose?.();
      fetchForVibe();
      return;
    }
    if (tab === 'url') {
      if (urlValid) startPublic.mutate();
      return;
    }
    if (selection.any) startLibrary.mutate();
  };

  let primaryLabel = 'Sync selected';
  let primaryHidden = false;
  let primaryDisabled = false;
  let meta = '';

  if (phase === 'complete') {
    primaryLabel = 'Play now';
  } else if (phase === 'progress' || phase === 'error') {
    primaryHidden = true;
  } else if (tab === 'url') {
    primaryLabel = 'Add playlist';
    primaryHidden = !signedIn;
    primaryDisabled = !urlValid;
    meta = !signedIn ? 'Spotify sign-in required' : urlValid ? 'Ready to add' : 'Paste a playlist link';
  } else if (library.isSuccess) {
    primaryDisabled = !selection.any;
    meta = selection.any
      ? `${selection.total} track${selection.total === 1 ? '' : 's'} selected`
      : 'Nothing selected';
  } else {
    primaryHidden = true;
  }

  const cancelLabel = phase === 'complete' || phase === 'error' ? 'Close' : 'Cancel';
  const tabsVisible = phase === 'pick';

  /* ------------------------------------------------------------------ body */

  const libraryUnavailable =
    tab === 'library' && !signedIn
      ? 'Sign in with Spotify to sync your library. Or use "Add public playlist" instead.'
      : tab === 'library' && library.isError
        ? 'Could not load your library. Check that you are signed in and the backend is running.'
        : null;

  return (
    <div className={styles.modal} role="presentation">
      <div className={styles.backdrop} onClick={close} aria-hidden="true" />
      <div
        className={styles.card}
        role="dialog"
        aria-modal="true"
        aria-labelledby="syncModalTitle"
        ref={cardRef}
        tabIndex={-1}
      >
        <header className={styles.header}>
          <h2 className={styles.title} id="syncModalTitle">
            Sync my Spotify library
          </h2>
          <button className={styles.close} type="button" aria-label="Close" onClick={close}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </header>

        {tabsVisible && (
          <div className={styles.tabs} role="tablist" aria-label="Sync source">
            <button
              className={styles.tab}
              type="button"
              role="tab"
              id="syncTabLibrary"
              aria-selected={tab === 'library'}
              aria-controls="syncPanelLibrary"
              onClick={() => setTab('library')}
            >
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <polyline points="23 4 23 10 17 10" />
                <polyline points="1 20 1 14 7 14" />
                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10" />
                <path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14" />
              </svg>
              <span>Sync my library</span>
            </button>
            <button
              className={styles.tab}
              type="button"
              role="tab"
              id="syncTabUrl"
              aria-selected={tab === 'url'}
              aria-controls="syncPanelUrl"
              onClick={() => setTab('url')}
            >
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
              </svg>
              <span>Add public playlist</span>
            </button>
          </div>
        )}

        <div className={styles.body}>
          {phase === 'error' && (
            <div className={styles.centered}>
              <p className={styles.hint}>{errorMsg || 'Something went wrong.'}</p>
              <button
                className={`${styles.btn} ${styles.btnSecondary}`}
                type="button"
                onClick={() => {
                  // Back to the picker the failure came from, selection intact.
                  setJobId(null);
                  setErrorMsg('');
                  setPhase('pick');
                }}
              >
                Retry
              </button>
            </div>
          )}

          {phase === 'progress' && (
            <div className={styles.view}>
              <div className={styles.progressBar} aria-hidden="true">
                <div className={styles.progressFill} style={{ width: `${pct}%` }} />
              </div>
              <div className={styles.progressMeta}>
                <span>{pct}%</span>
                <span>
                  {progress.processed || 0} / {progress.total || 0}
                </span>
              </div>
              <div className={styles.progressCurrent} aria-live="polite">
                {progress.current_track || '—'}
              </div>
              <div className={styles.stats}>
                <div title="Track was already fully analysed in the global library — added to your account and playable now">
                  <span className={styles.statLabel}>added</span>
                  <span className={styles.statNum}>{progress.added_to_library || 0}</span>
                </div>
                <div title="You already had this track — no action taken">
                  <span className={styles.statLabel}>already yours</span>
                  <span className={styles.statNum}>{progress.already_in_library || 0}</span>
                </div>
                <div title="New track — queued for offline audio analysis. Will appear in your library once the background worker finishes.">
                  <span className={styles.statLabel}>queued</span>
                  <span className={styles.statNum}>{progress.queued_for_analysis || 0}</span>
                </div>
              </div>
            </div>
          )}

          {phase === 'complete' && (
            <div className={`${styles.view} ${styles.centered}`}>
              <div className={styles.completeIcon} aria-hidden="true">
                <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              </div>
              <p className={styles.titleMini}>Library synced</p>
              <p className={styles.hint}>{summary || 'All done.'}</p>
            </div>
          )}

          {phase === 'pick' && tab === 'url' && (
            <div className={styles.view} role="tabpanel" id="syncPanelUrl" aria-labelledby="syncTabUrl">
              {signedIn ? (
                <>
                  <p className={styles.hint}>
                    Paste a link to any public Spotify playlist. Its tracks get added to your library.
                  </p>
                  <div className={styles.urlField}>
                    <label className={styles.urlInputWrap} data-state={urlError ? 'invalid' : urlState}>
                      <svg className={styles.urlIcon} viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                        <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
                      </svg>
                      <input
                        className={styles.urlInput}
                        type="url"
                        inputMode="url"
                        autoComplete="off"
                        spellCheck="false"
                        placeholder="https://open.spotify.com/playlist/..."
                        aria-label="Spotify playlist URL"
                        aria-describedby="syncUrlHint"
                        value={url}
                        onChange={(e) => {
                          setUrl(e.target.value);
                          setUrlError('');
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && urlValid) {
                            e.preventDefault();
                            startPublic.mutate();
                          }
                        }}
                      />
                    </label>
                    <div
                      className={styles.urlHint}
                      id="syncUrlHint"
                      data-state={urlError ? 'invalid' : urlState}
                    >
                      {urlHint}
                    </div>
                  </div>
                  <p className={styles.urlNote}>
                    Using your Spotify token to fetch the playlist. Only public playlists work.
                  </p>
                </>
              ) : (
                <div className={styles.centered}>
                  <div className={styles.signedOutIcon} aria-hidden="true">
                    <SpotifyMark size={28} />
                  </div>
                  <p className={styles.titleMini}>Sign in with Spotify to enable playlist URL sync</p>
                  <p className={styles.hint}>
                    Public playlist access requires a linked account — Spotify killed anonymous
                    access in late 2024.
                  </p>
                  <button
                    className={`${styles.btn} ${styles.btnPrimary}`}
                    type="button"
                    onClick={() => signIn()}
                  >
                    <SpotifyMark size={14} />
                    <span>Sign in with Spotify</span>
                  </button>
                </div>
              )}
            </div>
          )}

          {phase === 'pick' && tab === 'library' && (
            <div className={styles.view} role="tabpanel" id="syncPanelLibrary" aria-labelledby="syncTabLibrary">
              {libraryUnavailable ? (
                <div className={styles.centered}>
                  <p className={styles.hint}>{libraryUnavailable}</p>
                  {signedIn && (
                    <button
                      className={`${styles.btn} ${styles.btnSecondary}`}
                      type="button"
                      onClick={() => library.refetch()}
                    >
                      Retry
                    </button>
                  )}
                </div>
              ) : library.isPending ? (
                <div className={styles.centered}>
                  <div className={styles.spinner} aria-hidden="true" />
                  <p className={styles.hint}>Fetching your library…</p>
                </div>
              ) : (
                <>
                  <div className={styles.section}>
                    <label className={styles.row}>
                      <input
                        className={styles.check}
                        type="checkbox"
                        checked={liked}
                        onChange={(e) => setLiked(e.target.checked)}
                      />
                      <div className={styles.rowBody}>
                        <div className={styles.rowTitle}>Liked Songs</div>
                        <div className={styles.rowMeta}>{library.data?.liked_count || 0} tracks</div>
                      </div>
                    </label>
                    <label className={styles.row}>
                      <input
                        className={styles.check}
                        type="checkbox"
                        checked={top}
                        onChange={(e) => setTop(e.target.checked)}
                      />
                      <div className={styles.rowBody}>
                        <div className={styles.rowTitle}>Top Tracks</div>
                        <div className={styles.rowMeta}>
                          {library.data?.top_tracks_count || 0} tracks
                        </div>
                      </div>
                    </label>
                  </div>

                  <div className={styles.sectionTitle}>Your playlists</div>
                  <div className={styles.list}>
                    {playlists.length === 0 ? (
                      <div className={styles.hint}>No playlists found.</div>
                    ) : (
                      playlists.map((p) => (
                        <label className={styles.row} key={p.id}>
                          <input
                            className={styles.check}
                            type="checkbox"
                            checked={picked.has(p.id)}
                            onChange={() => togglePlaylist(p.id)}
                          />
                          <div className={styles.rowBody}>
                            <div className={styles.rowTitle}>{p.name || 'Untitled'}</div>
                            <div className={styles.rowMeta}>
                              <span>{p.track_count || 0} tracks</span>
                              {p.owner && (
                                <>
                                  <span className={styles.dot}>·</span>
                                  <span>{p.owner}</span>
                                </>
                              )}
                            </div>
                          </div>
                        </label>
                      ))
                    )}
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        <footer className={styles.footer}>
          <div className={styles.footerMeta}>{meta}</div>
          <div className={styles.footerActions}>
            <button className={`${styles.btn} ${styles.btnSecondary}`} type="button" onClick={close}>
              {cancelLabel}
            </button>
            {!primaryHidden && (
              <button
                className={`${styles.btn} ${styles.btnPrimary}`}
                type="button"
                disabled={primaryDisabled}
                onClick={onPrimary}
              >
                {primaryLabel}
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}

/**
 * Unmounting on close is deliberate: it resets every bit of modal state
 * (selection, pasted URL, job id, polling) the way legacy closeSyncModal()
 * did by hand, and it tears the status poll down with it.
 */
export default function SyncModal({ open, onClose, defaultTab }) {
  if (!open) return null;
  return <SyncModalInner onClose={onClose} defaultTab={defaultTab} />;
}
