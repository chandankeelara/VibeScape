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


function SyncModalInner({ onClose, defaultTab }) {
  const toast = useToast();
  const { fetchForVibe } = usePlayer();
  const queryClient = useQueryClient();
  const { token, isConnected, signIn, signOut } = useSpotifyAuth();
  const signedIn = isConnected;

  // Smart default (legacy openSyncModal): Spotify-connected users land on the
  // library tab; everyone else on url, their only working option.
  const [tab, setTab] = useState(defaultTab || (signedIn ? 'library' : 'url'));
  /*
   * The job itself lives in SyncJobProvider, above this modal, so closing the
   * window does not unmount the poll. This component now owns only which
   * SCREEN it is showing — picking sources versus watching a job — and reads
   * everything about the job from the provider.
   */
  const job = useSyncJob();
  const [picking, setPicking] = useState(true);

  const jobPhase = job ? job.phase : 'idle';
  // 'pick' while choosing or idle; otherwise mirror the job.
  const phase =
    picking && jobPhase === 'idle'
      ? 'pick'
      : jobPhase === 'running'
        ? 'progress'
        : jobPhase === 'complete'
          ? 'complete'
          : jobPhase === 'error'
            ? 'error'
            : 'pick';

  const setPhase = useCallback((next) => {
    // Only 'pick' is this component's to set now; the rest is the job's.
    if (next === 'pick') setPicking(true);
  }, []);

  const jobId = job ? job.jobId : null;
  const errorMsg = job ? job.errorMsg : '';
  const summary = job ? job.summary : '';

  const [liked, setLiked] = useState(true);
  const [top, setTop] = useState(false);
  const [picked, setPicked] = useState(() => new Set());

  const [url, setUrl] = useState('');
  const [urlError, setUrlError] = useState('');

  /*
   * Dismissing the modal leaves the job RUNNING.
   *
   * Legacy cancelled on every dismissal path — X, backdrop, Cancel, Escape —
   * which held the user hostage to a progress bar for the length of their
   * library. Importing music is something you should be able to start and
   * then walk away from, especially since the tracks that are instantly
   * playable land in the first second or two.
   *
   * Cancelling is still available, but it is now an explicit choice rather
   * than a side effect of closing a window. The job is keyed by id on the
   * server, so a later session could even reattach to it.
   */
  const close = useCallback(() => {
    onClose?.();
  }, [onClose]);

  /** The only path that actually stops the job. */
  const cancelJob = useCallback(() => {
    job?.cancel();
    onClose?.();
  }, [job, onClose]);

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
  /* Owned by SyncJobProvider — see the note at the top of this component.
     The modal only reads the result. */

  const progress = job ? job.progress : ZERO_PROGRESS;
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
    onMutate: () => setPicking(false),
    onSuccess: (j) => {
      if (!j?.job_id) {
        job?.fail('Could not start sync. Try again.');
        return;
      }
      if (j.note) toast(j.note, 'info');
      job?.begin(j.job_id);
    },
    onError: () => job?.fail('Could not start sync. Try again.'),
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
      setPicking(false);
    },
    onSuccess: (j) => {
      if (!j?.job_id) {
        toast('Could not add playlist. Try again.', 'error');
        setPicking(true);
        return;
      }
      if (j.note) toast(j.note, 'info');
      job?.begin(j.job_id);
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

  /*
   * Tracks that were already in the catalogue are playable the instant they
   * are linked — the server now front-loads them, so there is usually
   * something to listen to within a second or two of starting.
   */
  const readyNow = (progress.added_to_library || 0) + (progress.already_in_library || 0);

  const onPrimary = () => {
    // Leaving mid-sync is a first-class action, not an escape hatch: the job
    // keeps running and the library fills in behind the user while they
    // listen.
    if (phase === 'complete' || phase === 'progress') {
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
  } else if (phase === 'progress') {
    // Offered as soon as ONE track is playable rather than at 100%. Waiting
    // for the whole job made a 500-track import feel like a 500-track wait,
    // when most of the library was usable almost immediately.
    primaryHidden = readyNow < 1;
    primaryLabel = 'Start listening';
    meta = readyNow
      ? `${readyNow} ready to play — the rest keeps importing`
      : 'Finding tracks…';
  } else if (phase === 'error') {
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

  // Mid-sync the secondary button means "leave this running", so calling it
  // Cancel would be a lie. Cancelling moved to its own control inside the
  // progress view.
  const cancelLabel = phase === 'progress' ? 'Close' : (phase === 'complete' || phase === 'error' ? 'Close' : 'Cancel');
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
                  job?.dismiss();
                  setPicking(true);
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
              {/*
                * Two numbers, not four buckets. The old labels were the
                * server's internal result names — "added", "already yours"
                * and "queued" describe how the backend classified a row, not
                * anything the listener cares about. What they want to know is
                * what they can play right now and what is still coming.
                */}
              <div className={styles.stats}>
                <div title="Already analysed — in your library and playable right now">
                  <span className={styles.statLabel}>ready to play</span>
                  <span className={styles.statNum}>{readyNow}</span>
                </div>
                <div title="New to VibeScape — being analysed for mood and tempo before it can be played">
                  <span className={styles.statLabel}>still analysing</span>
                  <span className={styles.statNum}>{progress.queued_for_analysis || 0}</span>
                </div>
              </div>

              {/* Closing no longer stops the job, so stopping needs a control
                  of its own. Understated: leaving it running is the path we
                  want people to take. */}
              <button className={styles.linkBtn} type="button" onClick={cancelJob}>
                Stop importing
              </button>
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
