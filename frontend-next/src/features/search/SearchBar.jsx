import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import * as api from '../../lib/api';
import { apiKey, trackKey } from '../../lib/vibe';
import { emitDj } from '../../lib/djBus';
import { logEvent } from '../../lib/listenLog';
import { usePlayer } from '../../state/PlayerContext';
import { useSpotifyAuth } from '../../state/SpotifyAuthContext';
import { useToast } from '../../state/ToastContext';

import TrackRow from './TrackRow';
import { languageLabel } from '../queue/labels';
import { trackVibe } from './trackVibe';
import { useDebouncedValue } from './useDebouncedValue';
import { SearchIcon, CloseIcon, BackIcon, ClockIcon } from './icons';
import styles from './SearchBar.module.css';

const LIB_LIMIT = 15;

/*
 * Recent searches: the queries the user ACTED on (played or queued a
 * result), newest first. Per device, in localStorage; nothing is sent
 * anywhere. Shown when the field is clicked while empty, so the panel opens
 * onto something instead of nothing.
 */
const RECENT_KEY = 'vs.recentSearches';
const RECENT_MAX = 6;
function readRecentSearches() {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string').slice(0, RECENT_MAX) : [];
  } catch {
    return [];
  }
}
function writeRecentSearches(list) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch { /* not remembered */ }
}

/** Must match the phone breakpoint in SearchBar.module.css. */
const PHONE_QUERY = '(max-width: 767px)';
const isPhone = () =>
  typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia(PHONE_QUERY).matches;
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
 *
 * PHONE (<=767px, the S2 model). The bar is not shown inline at all: the top
 * bar's search icon asks the owner to open it (`mobileOpen`), and it then
 * takes the whole screen with a back button, results listed statically under
 * the field. `onMobileClose` hands control back; `onRequestMobileOpen` is how
 * Ctrl+K asks to be opened on a phone-width window. Above 767px all three are
 * ignored and the bar is always inline.
 */
export default function SearchBar({
  spotifyToken: tokenProp,
  className = '',
  mobileOpen = false,
  onMobileClose,
  onRequestMobileOpen,
}) {
  const { token, isConnected, signIn, getValidToken } = useSpotifyAuth();
  const spotifyToken = tokenProp ?? token ?? null;
  const spotifyEnabled = Boolean(spotifyToken) && (tokenProp ? true : isConnected);
  // Fetched at request time, not read from render: getValidToken refreshes
  // when the token is about to lapse, so a long session keeps searching.
  const currentToken = useCallback(
    async () => tokenProp ?? (await getValidToken()) ?? null,
    [tokenProp, getValidToken]
  );

  const { loadTrack, enqueue, enqueueAt, queue, setVibe, recent, current } = usePlayer();
  const [recentSearches, setRecentSearches] = useState(readRecentSearches);
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

  /* ------------------------------------------------------------ telemetry */
  /*
   * One `search` event per query the user acted on or walked away from —
   * not one per keystroke. Each settled query starts unlogged; playing or
   * queueing a result logs it with that outcome, and closing the panel logs
   * whatever is still unlogged as 'none'. resultsRef is declared further down
   * and only read when this runs, never during render.
   */
  const searchLogRef = useRef({ q: '', done: false });
  useEffect(() => { searchLogRef.current = { q: debouncedQ, done: false }; }, [debouncedQ]);
  const rememberSearch = useCallback((term) => {
    const t = term.trim();
    if (!t) return;
    setRecentSearches((prev) => {
      const next = [t, ...prev.filter((x) => x.toLowerCase() !== t.toLowerCase())].slice(0, RECENT_MAX);
      return next;
    });
  }, []);
  // Persisted from an effect: StrictMode double-invokes updaters, and an
  // updater that writes storage is a side effect React may run twice.
  useEffect(() => { writeRecentSearches(recentSearches); }, [recentSearches]);

  const logSearch = useCallback((outcome) => {
    const cur = searchLogRef.current;
    if (outcome === 'played' || outcome === 'queued') rememberSearch(cur.q);
    if (!cur.q || cur.done) return;
    cur.done = true;
    const { library: lib = [], spotify: sp = [] } = resultsRef.current || {};
    logEvent('search', {
      data: { query: cur.q, results_library: lib.length, results_spotify: sp.length, outcome },
    });
  }, [rememberSearch]);

  /* ----------------------------------------------------------- open/close */

  const close = useCallback(() => {
    logSearch('none');
    setOpen(false);
    setCursor(-1);
  }, [logSearch]);

  const reset = useCallback(({ blur = false } = {}) => {
    logSearch('none');
    setQuery('');
    setOpen(false);
    setCursor(-1);
    if (blur) inputRef.current?.blur();
  }, [logSearch]);

  // Leaves the phone overlay. Only meaningful at phone width; elsewhere the
  // bar is inline and there is nothing to close.
  const closeMobile = useCallback(() => {
    if (isPhone()) onMobileClose?.();
  }, [onMobileClose]);

  // The overlay opens onto a focused field: that is the whole reason the
  // user tapped the icon.
  useEffect(() => {
    if (!mobileOpen || !isPhone()) return;
    const id = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(id);
  }, [mobileOpen]);

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
        // On a phone-width window the field is not on screen until the
        // owner opens the overlay; the focus effect above takes it from there.
        if (isPhone() && !mobileOpen) {
          onRequestMobileOpen?.();
          return;
        }
        const input = inputRef.current;
        if (!input) return;
        input.focus();
        input.select();
        setOpen(true);
        return;
      }
      if (ev.key === 'Escape' && open) {
        ev.preventDefault();
        close();
        return;
      }
      if (ev.key === 'Escape' && mobileOpen && isPhone()) {
        ev.preventDefault();
        reset({ blur: true });
        onMobileClose?.();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, close, reset, mobileOpen, onMobileClose, onRequestMobileOpen]);

  /* -------------------------------------------------------------- actions */

  // Snap the global vibe to the picked track so the accent, slider and the
  // next random pick follow the song the user just chose.
  const syncVibe = useCallback((t) => {
    const v = trackVibe(t);
    if (v != null) setVibe(v);
  }, [setVibe]);

  const play = useCallback((t) => {
    logSearch('played');
    reset({ blur: true });
    syncVibe(t);
    // Explicit intent: fire a 'searched' event so the next DJ fetch sees
    // the picked song's vibe immediately, instead of waiting for the end
    // of playback. useDj listens on djBus and logs it; how the song then
    // ends is logged as its own, later event.
    const key = apiKey(t);
    if (key) emitDj({ track_id: key, action: 'searched', played_ratio: null, ts: Date.now() });
    loadTrack(t, { source: 'search', trigger: 'search' });
    closeMobile();
  }, [logSearch, reset, syncVibe, loadTrack, closeMobile]);

  /*
   * Replaying from the empty panel's "Recently played" is a pick off a list,
   * not a search — logged to the DJ as 'picked', exactly like the recent
   * trail on the player, so it cannot be mistaken for search intent.
   */
  const playRecent = useCallback((t) => {
    reset({ blur: true });
    const key = apiKey(t);
    if (key) emitDj({ track_id: key, action: 'picked', played_ratio: null, ts: Date.now() });
    loadTrack(t, { source: 'pick', trigger: 'pick', reason: 'recent' });
    closeMobile();
  }, [reset, loadTrack, closeMobile]);

  /** Fill the field from a recent search or a suggestion, keeping focus. */
  const runQuery = useCallback((term) => {
    setQuery(term);
    setCursor(-1);
    setOpen(true);
    inputRef.current?.focus();
  }, []);

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
      logSearch('queued');
      enqueueAt(t, index, { via: 'drag' });
      emitQueued(t);
      toast('Added to queue.', 'success');
    };
    document.addEventListener('vibescape:queue-drop', onDrop);
    return () => document.removeEventListener('vibescape:queue-drop', onDrop);
  }, [enqueueAt, emitQueued, toast, logSearch]);

  const addToQueue = useCallback((t) => {
    const already = queue.some((x) => trackKey(x) === trackKey(t));
    if (!already) logSearch('queued');
    enqueue(t, { via: 'search' });
    if (!already) emitQueued(t);
    toast(already ? 'Already in the queue.' : 'Added to queue.', already ? 'info' : 'success');
  }, [queue, enqueue, emitQueued, toast, logSearch]);

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
      closeMobile();
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
    // Stays open when emptied: the panel falls back to recents and hints.
    setOpen(true);
  };

  /* --------------------------------------------------------------- render */

  const hasQuery = q.length > 0;
  const showNone = !loading && hasQuery && !rows.length && !spotifyErr && !libQuery.error;
  const showResults = !loading && hasQuery && !showNone;
  const spotifyIndexBase = library.length;

  return (
    <div
      className={`${styles.bar} ${mobileOpen ? styles.mobileOpen : ''} ${className}`}
      ref={rootRef}
      role="search"
    >
      <div className={styles.inner}>
        <div className={`${styles.inputWrap} ${hasQuery ? styles.hasQuery : ''}`}>
          <button
            className={styles.back}
            type="button"
            aria-label="Close search"
            onClick={() => { reset({ blur: true }); onMobileClose?.(); }}
          >
            <BackIcon />
          </button>
          <SearchIcon className={styles.inputIcon} />
          <input
            ref={inputRef}
            className={styles.input}
            type="search"
            autoComplete="off"
            spellCheck="false"
            placeholder="Search songs, artists, albums"
            aria-label={spotifyEnabled ? 'Search your library and Spotify' : 'Search your library'}
            role="combobox"
            aria-autocomplete="list"
            aria-controls={listboxId}
            aria-expanded={open}
            aria-activedescendant={open && cursor >= 0 ? rowId(cursor) : undefined}
            value={query}
            onChange={onChange}
            onFocus={() => setOpen(true)}
            onClick={() => setOpen(true)}
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

        {open && !hasQuery && (
          <div className={styles.dropdown}>
            <EmptyPanel
              recentSearches={recentSearches}
              onRunQuery={runQuery}
              onClearSearches={() => setRecentSearches([])}
              recentTracks={recent
                .filter((t) => !current || trackKey(t) !== trackKey(current))
                .slice(-5)
                .reverse()}
              onPlayRecent={playRecent}
              onQueue={addToQueue}
              current={current}
              spotifyEnabled={spotifyEnabled}
            />
          </div>
        )}

        {open && hasQuery && (
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
                    <span className={styles.sectionHint}>{library.length}</span>
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

              <div className={styles.section} role="group" aria-label="On Spotify">
                <div className={styles.sectionTitle}>
                  <span>On Spotify</span>
                  {spotifyEnabled && spotifyTracks.length > 0 && (
                    <span className={styles.sectionHint}>{spotifyTracks.length}</span>
                  )}
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

/**
 * What the panel shows when the field is clicked while empty — so a click
 * visibly does something before the user has typed.
 *   - recent searches (queries the user acted on), click to re-run
 *   - recently played, playable and queueable
 *   - a one-line hint of what can be searched, with examples taken from the
 *     user's own current track, so the examples always have results
 */
function EmptyPanel({
  recentSearches, onRunQuery, onClearSearches, recentTracks, onPlayRecent, onQueue, current, spotifyEnabled,
}) {
  const examples = [current?.artist, languageLabel(current?.language || ''), current?.album]
    .filter(Boolean)
    .filter((x, i, a) => a.indexOf(x) === i)
    .slice(0, 3);

  return (
    <div className={styles.empty}>
      {recentSearches.length > 0 && (
        <div className={styles.section} role="group" aria-label="Recent searches">
          <div className={styles.sectionTitle}>
            <span>Recent searches</span>
            <span className={styles.sectionHint}>
              <button className={styles.linkBtn} type="button" onClick={onClearSearches}>Clear</button>
            </span>
          </div>
          {recentSearches.map((term) => (
            <button key={term} type="button" className={styles.recentRow} onClick={() => onRunQuery(term)}>
              <ClockIcon />
              <span>{term}</span>
            </button>
          ))}
        </div>
      )}

      {recentTracks.length > 0 && (
        <div className={styles.section} role="group" aria-label="Recently played">
          <div className={styles.sectionTitle}><span>Recently played</span></div>
          {recentTracks.map((t, i) => (
            <TrackRow
              key={`recent-${trackKey(t) || i}`}
              track={t}
              variant="library"
              onActivate={() => onPlayRecent(t)}
              onQueue={() => onQueue(t)}
            />
          ))}
        </div>
      )}

      <div className={styles.tip}>
        <p>
          Search {spotifyEnabled ? 'your library and Spotify' : 'your library'} by song, artist, album or language.
        </p>
        {examples.length > 0 && (
          <div className={styles.chips}>
            <span className={styles.tryLabel}>Try</span>
            {examples.map((ex) => (
              <button key={ex} type="button" className={styles.chip} onClick={() => onRunQuery(ex)}>{ex}</button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
