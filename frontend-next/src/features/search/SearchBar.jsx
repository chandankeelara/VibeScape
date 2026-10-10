import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import * as api from '../../lib/api';
import { apiKey, trackKey } from '../../lib/vibe';
import { emitDj } from '../../lib/djBus';
import { usePlayer } from '../../state/PlayerContext';
import { useSpotifyAuth } from '../../state/SpotifyAuthContext';
import { useToast } from '../../state/ToastContext';

import TrackRow from './TrackRow';
import { trackVibe } from './trackVibe';
import { useDebouncedValue } from './useDebouncedValue';
import { SearchIcon, CloseIcon } from './icons';
import styles from './SearchBar.module.css';

const LIB_LIMIT = 15;
const SPOTIFY_LIMIT = 10;

/** Pull a human message out of an ApiError from /api/ingest/single. */
function ingestErrorMessage(err, fallback) {
  const detail = err?.body?.detail;
  if (detail?.error === 'no_preview_available') return 'No preview available for that track.';
  if (detail?.error === 'spotify_token_expired') return 'Spotify session expired.';
  if (detail?.message) return detail.message;
  if (typeof detail === 'string') return detail;
  return fallback;
}

function spotifySearchErrorMessage(err) {
  if (err?.status === 401) return 'Spotify session expired — reconnect Spotify.';
  const msg = err?.body?.detail?.message;
  return msg ? `Spotify: ${msg}` : 'Spotify search failed.';
}

/**
 * Persistent search bar over the user's library + the Spotify catalog.
 *
 * Ported from frontend/app.js:4173-4800 and frontend/index.html:176-200. All
 * of the legacy HTML-string building (and its hand-rolled escapeHtml) is gone;
 * rows render through the single <TrackRow /> component.
 *
 * Spotify catalog search needs the user's OAuth token, which comes from
 * SpotifyAuthContext. When Spotify isn't connected the bar degrades to
 * library-only search and offers a connect link — same as the legacy app for
 * a signed-out user. `spotifyToken` can be passed to override the context
 * (tests, or a host that already holds a token).
 */
export default function SearchBar({ spotifyToken: tokenProp, className = '' }) {
  const { token, isConnected, signIn, getValidToken } = useSpotifyAuth();
  const spotifyToken = tokenProp ?? token ?? null;
  const spotifyEnabled = Boolean(spotifyToken) && (tokenProp ? true : isConnected);
  // Fetched at request time, not read from render: getValidToken refreshes
  // when the token is about to lapse, so a long session keeps searching.
  const currentToken = useCallback(
    async () => tokenProp ?? (await getValidToken()) ?? null,
    [tokenProp, getValidToken]
  );

  const { loadTrack, enqueue, enqueueAt, queue, setVibe } = usePlayer();
  const toast = useToast();
  const queryClient = useQueryClient();

  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(-1);
  // { id: spotify_id, kind: 'add' | 'queue' } — which trailing control is pending.
  const [busy, setBusy] = useState(null);

  const rootRef = useRef(null);
  const inputRef = useRef(null);

  const uid = useId();
  const listboxId = `${uid}-listbox`;
  const rowId = (i) => `${uid}-row-${i}`;

  const q = query.trim();
  const debouncedQ = useDebouncedValue(q, 220);

  /* --------------------------------------------------------------- queries */

  const libKey = useMemo(() => ['search', 'library', debouncedQ], [debouncedQ]);
  const spKey = useMemo(() => ['search', 'spotify', debouncedQ], [debouncedQ]);

  const libQuery = useQuery({
    queryKey: libKey,
    queryFn: () => api.searchTracks({ q: debouncedQ, limit: LIB_LIMIT }),
    enabled: Boolean(debouncedQ),
    staleTime: 30_000,
  });

  const spQuery = useQuery({
    queryKey: spKey,
    queryFn: async () => api.spotifySearch({ q: debouncedQ, limit: SPOTIFY_LIMIT }, await currentToken()),
    enabled: Boolean(debouncedQ) && spotifyEnabled,
    staleTime: 30_000,
    retry: false,
  });

  const library = libQuery.data?.tracks ?? [];
  const spotifyTracks = spQuery.data?.tracks ?? [];
  const spotifyErr = spQuery.error ? spotifySearchErrorMessage(spQuery.error) : null;

  // "Loading" covers the debounce gap too, so the panel doesn't flash the
  // previous query's results between keystrokes.
  const loading = Boolean(q) && (q !== debouncedQ || libQuery.isPending || (spotifyEnabled && spQuery.isPending));

  /* ------------------------------------------------------------ flat rows */
  // One ordered list of every rendered row, so arrow keys can walk library
  // and Spotify results as a single sequence.
  const rows = useMemo(() => {
    const out = [];
    library.forEach((t) => out.push({ variant: 'library', track: t }));
    spotifyTracks.forEach((t) => out.push({ variant: 'spotify', track: t }));
    return out;
  }, [library, spotifyTracks]);

  // A new result set invalidates the old cursor position.
  useEffect(() => { setCursor(-1); }, [debouncedQ, rows.length]);

  useEffect(() => {
    if (cursor < 0) return;
    document.getElementById(rowId(cursor))?.scrollIntoView({ block: 'nearest' });
  }, [cursor]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ----------------------------------------------------------- open/close */

  const close = useCallback(() => {
    setOpen(false);
    setCursor(-1);
  }, []);

  const reset = useCallback(({ blur = false } = {}) => {
    setQuery('');
    setOpen(false);
    setCursor(-1);
    if (blur) inputRef.current?.blur();
  }, []);

  // Click outside collapses the panel; the input itself stays put.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (ev) => {
      if (rootRef.current?.contains(ev.target)) return;
      close();
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open, close]);

  // Ctrl/Cmd+K focuses the input from anywhere, Escape collapses the panel
  // when focus has moved elsewhere.
  useEffect(() => {
    const onKey = (ev) => {
      if ((ev.ctrlKey || ev.metaKey) && (ev.key === 'k' || ev.key === 'K')) {
        ev.preventDefault();
        const input = inputRef.current;
        if (!input) return;
        input.focus();
        input.select();
        if (input.value.trim()) setOpen(true);
        return;
      }
      if (ev.key === 'Escape' && open) {
        ev.preventDefault();
        close();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, close]);

  /* -------------------------------------------------------------- actions */

  // Snap the global vibe to the picked track so the accent, slider and the
  // next random pick follow the song the user just chose.
  const syncVibe = useCallback((t) => {
    const v = trackVibe(t);
    if (v != null) setVibe(v);
  }, [setVibe]);

  const play = useCallback((t) => {
    reset({ blur: true });
    syncVibe(t);
    // Explicit intent: fire a 'searched' event so the next DJ fetch sees
    // the picked song's vibe immediately, instead of waiting for the end
    // of playback. useDj listens on djBus and logs it; how the song then
    // ends is logged as its own, later event.
    const key = apiKey(t);
    if (key) emitDj({ track_id: key, action: 'searched', played_ratio: null, ts: Date.now() });
    loadTrack(t);
  }, [reset, syncVibe, loadTrack]);

  /* ------------------------------------------------- drag into the queue */
  /*
   * The queue's drag engine can't resolve a search row to a track — the result
   * lists live here — so on drop it dispatches `vibescape:queue-drop` with the
   * row's key and the target index, and we do the lookup. Keeping the current
   * results in a ref means the document listener binds once instead of on
   * every keystroke.
   */
  const resultsRef = useRef({ library: [], spotify: [] });
  resultsRef.current = { library, spotify: spotifyTracks };

  // Dropped-into-queue and "+ queue" from search are both explicit "I want
  // this" acts, same signal strength as queueing a sidebar rec. Fire 'queued'
  // through djBus so the event log picks it up without SearchBar having to
  // import useDj (which would double-instantiate the log's state).
  const emitQueued = useCallback((t) => {
    const key = apiKey(t);
    if (key) emitDj({ track_id: key, action: 'queued', played_ratio: null, ts: Date.now() });
  }, []);

  useEffect(() => {
    const onDrop = (ev) => {
      const { key, index } = ev.detail ?? {};
      if (!key || typeof index !== 'number' || index < 0) return;
      const { library: lib, spotify: sp } = resultsRef.current;
      const match = (t) =>
        trackKey(t) === key || String(t.spotify_id ?? '') === key || String(t.id ?? '') === key;
      const t = lib.find(match) ?? sp.find(match);
      if (!t) return;
      enqueueAt(t, index);
      emitQueued(t);
      toast('Added to queue.', 'success');
    };
    document.addEventListener('vibescape:queue-drop', onDrop);
    return () => document.removeEventListener('vibescape:queue-drop', onDrop);
  }, [enqueueAt, emitQueued, toast]);

  const addToQueue = useCallback((t) => {
    const already = queue.some((x) => trackKey(x) === trackKey(t));
    enqueue(t);
    if (!already) emitQueued(t);
    toast(already ? 'Already in the queue.' : 'Added to queue.', already ? 'info' : 'success');
  }, [queue, enqueue, emitQueued, toast]);

  /**
   * /api/ingest/single is idempotent and covers all three states: already
   * linked (returns the row), exists globally but unlinked (links + returns),
   * and brand new (runs the pipeline + returns). That is why "play an
   * in-library Spotify result" and "add a new one" are the same call.
   */
  const ingest = useCallback(async (spotifyId) => {
    const body = { spotify_id: spotifyId };
    const tok = await currentToken();
    if (tok) body.access_token = tok;
    const res = await api.ingestSingle(body, tok);
    return res?.track ?? null;
  }, [currentToken]);

  /** Keep the cached result lists consistent with the DB after an ingest. */
  const markInLibrary = useCallback((spotifyId, track) => {
    queryClient.setQueryData(spKey, (old) =>
      old?.tracks
        ? { ...old, tracks: old.tracks.map((t) => (t.spotify_id === spotifyId ? { ...t, in_library: true } : t)) }
        : old
    );
    if (!track) return;
    queryClient.setQueryData(libKey, (old) => {
      const list = old?.tracks ?? [];
      if (list.some((t) => t.spotify_id === spotifyId)) return old;
      return { ...(old ?? {}), tracks: [track, ...list] };
    });
  }, [queryClient, libKey, spKey]);

  const playSpotify = useCallback(async (t) => {
    try {
      const row = await ingest(t.spotify_id);
      if (!row) throw new Error('no track');
      markInLibrary(t.spotify_id, row);
      play(row);
    } catch (e) {
      toast(ingestErrorMessage(e, 'Could not open that track.'), 'error');
    }
  }, [ingest, markInLibrary, play, toast]);

  const addSpotify = useCallback(async (t) => {
    setBusy({ id: t.spotify_id, kind: 'add' });
    try {
      const row = await ingest(t.spotify_id);
      toast('Added to your library.', 'success');
      markInLibrary(t.spotify_id, row);
      if (row) play(row);
    } catch (e) {
      toast(ingestErrorMessage(e, 'Could not add track.'), 'error');
    } finally {
      setBusy(null);
    }
  }, [ingest, markInLibrary, play, toast]);

  const queueSpotify = useCallback(async (t) => {
    // Already ours — the cached row carries enough (title/artist/artwork) to
    // render in the queue; loadTrack resolves playback when it advances.
    const libCached = library.find((x) => x.spotify_id === t.spotify_id);
    if (libCached) { addToQueue(libCached); return; }
    if (t.in_library) { addToQueue(t); return; }

    setBusy({ id: t.spotify_id, kind: 'queue' });
    try {
      const row = await ingest(t.spotify_id);
      if (!row) throw new Error('no track');
      markInLibrary(t.spotify_id, row);
      addToQueue(row);
    } catch (e) {
      toast(ingestErrorMessage(e, 'Could not queue track.'), 'error');
    } finally {
      setBusy(null);
    }
  }, [library, addToQueue, ingest, markInLibrary, toast]);

  /** The row-level primary action, or null for a non-interactive row. */
  const activateFor = useCallback((row) => {
    if (row.variant === 'library') return () => play(row.track);
    // A Spotify result that isn't in the library has "Add" as its only
    // affordance — the row itself does nothing (legacy data-action="noop").
    if (row.track.in_library) return () => playSpotify(row.track);
    return null;
  }, [play, playSpotify]);

  /* ------------------------------------------------------------- keyboard */

  const onInputKeyDown = (ev) => {
    if (ev.key === 'Escape') {
      ev.preventDefault();
      reset({ blur: true });
      return;
    }
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      if (!rows.length) return;
      ev.preventDefault();
      if (!open) setOpen(true);
      const delta = ev.key === 'ArrowDown' ? 1 : -1;
      setCursor((c) => {
        const n = rows.length;
        if (c < 0) return delta > 0 ? 0 : n - 1;
        return (c + delta + n) % n;
      });
      return;
    }
    if (ev.key === 'Enter' && cursor >= 0 && rows[cursor]) {
      const activate = activateFor(rows[cursor]);
      if (activate) {
        ev.preventDefault();
        activate();
      }
    }
  };

  const onChange = (ev) => {
    const v = ev.target.value;
    setQuery(v);
    setCursor(-1);
    setOpen(Boolean(v.trim()));
  };

  /* --------------------------------------------------------------- render */

  const hasQuery = q.length > 0;
  const showNone = !loading && hasQuery && !rows.length && !spotifyErr && !libQuery.error;
  const showResults = !loading && hasQuery && !showNone;
  const spotifyIndexBase = library.length;

  return (
    <div className={`${styles.bar} ${className}`} ref={rootRef}>
      <div className={styles.inner}>
        <div className={`${styles.inputWrap} ${hasQuery ? styles.hasQuery : ''}`}>
          <SearchIcon className={styles.inputIcon} />
          <input
            ref={inputRef}
            className={styles.input}
            type="search"
            autoComplete="off"
            spellCheck="false"
            placeholder={spotifyEnabled ? 'Search your library and Spotify…' : 'Search your library…'}
            aria-label="Search query"
            role="combobox"
            aria-autocomplete="list"
            aria-controls={listboxId}
            aria-expanded={open}
            aria-activedescendant={open && cursor >= 0 ? rowId(cursor) : undefined}
            value={query}
            onChange={onChange}
            onFocus={() => { if (q) setOpen(true); }}
            onKeyDown={onInputKeyDown}
          />
          <span className={styles.kbd} aria-hidden="true">
            <kbd>Ctrl</kbd><kbd>K</kbd>
          </span>
          {hasQuery && (
            <button
              className={styles.clear}
              type="button"
              aria-label="Clear search"
              onClick={() => { reset(); inputRef.current?.focus(); }}
            >
              <CloseIcon />
            </button>
          )}
        </div>

        {open && (
          <div className={styles.dropdown}>
            {loading && (
              <div className={styles.stage} role="status">
                <div className={styles.spinner} aria-hidden="true" />
                <p className={styles.hint}>Searching&#8230;</p>
              </div>
            )}

            {showNone && (
              <div className={styles.stage}>
                <p className={styles.hint}>No matches. Try a different query.</p>
              </div>
            )}

            <div
              id={listboxId}
              className={styles.results}
              role="listbox"
              aria-label="Search results"
              hidden={!showResults}
            >
              <div className={styles.section} role="group" aria-label="Your library">
                <div className={styles.sectionTitle}>
                  <span>Your library</span>
                  {library.length > 0 && (
                    <span className={styles.sectionHint}>
                      {library.length} match{library.length === 1 ? '' : 'es'}
                    </span>
                  )}
                </div>
                {libQuery.error ? (
                  <div className={`${styles.status} ${styles.statusError}`}>
                    Library search failed. Try again.
                  </div>
                ) : library.length ? (
                  library.map((t, i) => (
                    <TrackRow
                      key={`lib-${t.spotify_id || t.id || i}`}
                      id={rowId(i)}
                      track={t}
                      variant="library"
                      dragKey={trackKey(t)}
                      selected={cursor === i}
                      onActivate={() => play(t)}
                      onQueue={() => addToQueue(t)}
                    />
                  ))
                ) : (
                  <div className={styles.status}>No matches in your library.</div>
                )}
              </div>

              <div className={styles.section} role="group" aria-label="From Spotify">
                <div className={styles.sectionTitle}>
                  <span>From Spotify</span>
                  {!spotifyEnabled && (
                    <span className={styles.sectionHint}>
                      <button className={styles.linkBtn} type="button" onClick={() => signIn()}>
                        Connect Spotify
                      </button>
                      {' to enable'}
                    </span>
                  )}
                </div>
                {!spotifyEnabled ? (
                  <div className={styles.status}>
                    Spotify catalog search is disabled until you connect Spotify.
                  </div>
                ) : spotifyErr ? (
                  <div className={`${styles.status} ${styles.statusError}`}>{spotifyErr}</div>
                ) : spotifyTracks.length ? (
                  spotifyTracks.map((t, i) => {
                    const idx = spotifyIndexBase + i;
                    const pending = busy?.id === t.spotify_id ? busy.kind : null;
                    return (
                      <TrackRow
                        key={`sp-${t.spotify_id || i}`}
                        id={rowId(idx)}
                        track={t}
                        variant="spotify"
                        inLibrary={Boolean(t.in_library)}
                        busy={pending}
                        selected={cursor === idx}
                        onActivate={t.in_library ? () => playSpotify(t) : undefined}
                        onQueue={() => queueSpotify(t)}
                        onAdd={() => addSpotify(t)}
                      />
                    );
                  })
                ) : (
                  <div className={styles.status}>No matches on Spotify.</div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
