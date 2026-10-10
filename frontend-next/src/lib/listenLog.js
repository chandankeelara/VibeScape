/**
 * What actually happened to a track, and why it stopped.
 *
 * A plain module with no React lifecycle, deliberately — the same reasoning as
 * lib/mascotBus.js and the whole of src/media/. Three consequences matter:
 *
 *   - It is immune to StrictMode. Nothing here runs from an effect or a state
 *     updater, so nothing double-fires. The media layer calls into it from DOM
 *     event handlers and from player.loadTrack(), neither of which React
 *     replays.
 *   - It holds no React state, so nothing it does can re-render the player.
 *   - Exactly-once is structural, not defensive: there is at most ONE open
 *     play at a time (`open`). endPlay() emits and nulls it; a second call
 *     finds null and returns. That single invariant is what makes a duplicate
 *     'ended' event, a pagehide followed by a real transition, or an unmount
 *     during teardown all harmless.
 *
 * WHERE THE ATTRIBUTION IS HOOKED (this is the whole value of the feature —
 * a skip misreported as a completion trains the recommender backwards):
 *
 *   completed  src/media/player.js, at the three real end-of-media events —
 *              <audio> 'ended', youtube onEnded, and spotify's inferred
 *              onEnded. Those fire BEFORE hooks.onEnded() hands control to
 *              React's next(), so by the time the replacement track reaches
 *              loadTrack() the play is already closed and cannot be
 *              re-attributed as a skip.
 *   skipped    the default for any transition that reaches player.loadTrack()
 *              with a play still open. If the track did not end itself and
 *              something is now taking its place, a human cut it short: the
 *              next/prev buttons, a media key, a queue jump, a search pick, a
 *              rec, a mood-slider re-roll.
 *   replaced   passed explicitly by the few programmatic paths — player.stop()
 *              (sign-out, empty library) and the post-library-sync re-roll.
 *   abandoned  the page went away with a play still open — pagehide, or (when
 *              the browser killed the page without one) recovered on the next
 *              launch from the copy kept in localStorage. Counted in end_count
 *              and the time totals, in no completion/skip bucket.
 *
 * TELEMETRY V2 (2026-10-10). Every event carries session_id and
 * tz_offset_min. play_end also carries listened_ms — audible time, accrued
 * only while the media layer reports playing, so pauses and seek jumps do not
 * count (position_ms is where the playhead stopped, which seeking distorts) —
 * plus `trigger` (how it was ended) and `playback` (what actually played).
 * pause / resume / seek are logged from the player's USER commands only, never
 * from element events, which also fire on every internal track switch.
 *
 * A reload of the SAME track is not an end. player.loadTrack() runs again when
 * a Spotify device appears mid-preview and when leaving video mode; both would
 * otherwise bill one listen as a skip plus a fresh play.
 */

import { enqueue, flush } from './events';

/** Open play, or null. The single source of exactly-once. */
let open = null;

/**
 * Where an open play is mirrored, so a killed page can be closed next launch.
 * One slot PER PAGE (prefix + pageId): with a single shared slot, a second
 * tab would "recover" the first tab's live play as abandoned.
 */
const OPEN_PLAY_PREFIX = 'vibescape.openPlay.';
/** Heartbeat for this page's slot while a play is open, playing or paused. */
const OPEN_PLAY_SAVE_MS = 15_000;
/** A slot whose heartbeat is older than this belongs to a dead page. */
const OPEN_PLAY_STALE_MS = 3 * OPEN_PLAY_SAVE_MS;
/** A tab hidden this long with nothing playing starts a new session on return. */
const SESSION_IDLE_MS = 30 * 60 * 1000;

function newId() {
  try { if (crypto?.randomUUID) return crypto.randomUUID(); } catch { /* old browser */ }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

let pageId = null; // assigned after newId() is defined below

/** One per app session — page load, or a return after SESSION_IDLE_MS away. */
let session = { id: newId(), startedAt: Date.now(), hiddenAt: null };
pageId = newId();
const openKey = () => OPEN_PLAY_PREFIX + pageId;
export const sessionId = () => session.id;

/** Minutes EAST of UTC (IST = +330, EDT = -240), as the backend stores it. */
const tzOffset = () => -new Date().getTimezoneOffset();

/** Live playhead, registered by the media layer (it owns the clock, and this
 *  module must not import it back — that would be a cycle). */
let clock = () => ({ position_ms: null, duration_ms: null });

/** Live vibe / dj-mode / vibe-source, registered by PlayerContext from refs. */
let context = () => ({});

let installed = false;

export function setClock(fn) { if (typeof fn === 'function') clock = fn; }
export function setContextProvider(fn) { if (typeof fn === 'function') context = fn; }

function install() {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  recoverAbandoned();
  startSession();
  // Close the open play before the page goes, so a user who leaves mid-track
  // still contributes the thing we most want to know — how far they got.
  // Not on visibilitychange: a hidden tab keeps playing, and ending the play
  // there would invent a stop that never happened.
  window.addEventListener('pagehide', () => {
    endPlay('abandoned');
    endSession(Date.now());
    flush({ unload: true });
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      session.hiddenAt = Date.now();
      saveOpen();
      return;
    }
    // Back after a long absence with nothing playing: that was a new visit.
    // A hidden tab that kept playing music is still the same session.
    const away = session.hiddenAt ? Date.now() - session.hiddenAt : 0;
    const playing = !!open?.playingSince;
    if (away > SESSION_IDLE_MS && !playing) {
      endSession(session.hiddenAt);
      session = { id: newId(), startedAt: Date.now(), hiddenAt: null };
      startSession();
    }
    session.hiddenAt = null;
  });
  setInterval(() => { if (open) saveOpen(); }, OPEN_PLAY_SAVE_MS);
}

function startSession() {
  let platform = 'web';
  try {
    if (window.matchMedia?.('(display-mode: standalone)').matches) platform = 'pwa';
  } catch { /* no matchMedia */ }
  emit({ type: 'session_start', data: { platform } });
}

function endSession(at) {
  emit({ type: 'session_end', data: { duration_ms: Math.max(0, at - session.startedAt) } }, { ts: at });
}

/* ----------------------------------------------------- abandoned recovery */

function heardSoFar(play, now = Date.now()) {
  return play.listenedMs + (play.playingSince ? now - play.playingSince : 0);
}

/** Mirror the open play. Cheap, and never allowed to throw. */
function saveOpen() {
  try {
    if (!open) { localStorage.removeItem(openKey()); return; }
    const t = clock() || {};
    localStorage.setItem(openKey(), JSON.stringify({
      id: open.id,
      source: open.source,
      playback: open.playback,
      listened_ms: Math.round(heardSoFar(open)),
      position_ms: Number.isFinite(t.position_ms) ? t.position_ms : null,
      duration_ms: Number.isFinite(t.duration_ms) && t.duration_ms > 0 ? t.duration_ms : null,
      session_id: session.id,
      saved_at: Date.now(),
    }));
  } catch { /* private mode / quota */ }
}

/**
 * A page died with a play open (mobile browsers often kill a backgrounded tab
 * without pagehide). Close it with what it last saved, stamped at the time it
 * saved and with that page's session id. Only stale slots are touched — a
 * live tab keeps its heartbeat fresh. Runs on install and on every new play.
 */
function recoverAbandoned() {
  let stale = [];
  try {
    const now = Date.now();
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k || !k.startsWith(OPEN_PLAY_PREFIX) || k === openKey()) continue;
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(k) || 'null'); } catch { /* corrupt */ }
      if (!saved || now - (saved.saved_at || 0) > OPEN_PLAY_STALE_MS) stale.push([k, saved]);
    }
    stale.forEach(([k]) => localStorage.removeItem(k));
  } catch { return; }
  stale.forEach(([, saved]) => { if (saved?.id) sendAbandoned(saved); });
}

function sendAbandoned(saved) {
  enqueue({
    type: 'play_end',
    ...saved.id,
    reason: 'abandoned',
    ...(saved.position_ms != null ? { position_ms: saved.position_ms } : null),
    ...(saved.duration_ms != null ? { duration_ms: saved.duration_ms } : null),
    listened_ms: saved.listened_ms || 0,
    ...(saved.playback ? { playback: saved.playback } : null),
    ...(saved.source ? { source: saved.source } : null),
    session_id: saved.session_id || undefined,
    tz_offset_min: tzOffset(),
    client_ts: saved.saved_at || Date.now(),
    // No vibe context: the slider as it is NOW says nothing about then.
  });
}

/** Identity the backend can resolve: spotify_id, else the internal tracks.id. */
function identify(track) {
  const sid = typeof track?.spotify_id === 'string' ? track.spotify_id.trim() : '';
  if (sid) return { spotify_id: sid };
  const n = Number(track?.id);
  return Number.isFinite(n) && n > 0 ? { track_id: n } : null;
}

function emit(fields, { ts } = {}) {
  const { vibe, vibe_source: vibeSource, dj_mode: djMode } = context() || {};
  const ev = { ...fields, client_ts: ts || Date.now(), session_id: session.id, tz_offset_min: tzOffset() };
  if (Number.isFinite(vibe)) ev.vibe = vibe;
  // Unknown provenance sends NOTHING rather than a guess. An unlabelled
  // system-set vibe would train the recommender on its own echo.
  if (vibeSource === 'user' || vibeSource === 'system') ev.vibe_source = vibeSource;
  if (typeof djMode === 'boolean') ev.dj_mode = djMode;
  enqueue(ev);
}

/** Is `track` the one currently open? Used to tell a source switch from a
 *  real track change. */
export function isOpen(track) {
  if (!open) return false;
  const id = identify(track);
  if (!id) return false;
  return id.spotify_id
    ? id.spotify_id === open.id.spotify_id
    : id.track_id === open.id.track_id;
}

/**
 * Where the pick came from. 'pick' (2026-10-10) is a song played straight
 * off a list — a rec, the recent trail, prev. Before that those were logged
 * as 'search' (or 'dj' for a rec in DJ mode), so older 'search' rows mix
 * both. [backend contract — documented in docs/backend-todo.md]
 */
const SOURCES = new Set(['queue', 'dj', 'search', 'pick', 'autoplay']);
const PLAYBACKS = new Set(['spotify', 'preview', 'youtube']);

/**
 * A track started playing. `source` is where the pick came from, `playback`
 * what is about to play it. Unknown values are dropped rather than sent as
 * something the contract doesn't define.
 */
export function startPlay(track, { source, playback } = {}) {
  install();
  recoverAbandoned();
  const id = identify(track);
  if (!id) return; // un-ingested Spotify result — the backend cannot resolve it
  // Defensive only; every caller ends first. Without a reason it lands in no
  // bucket, which is the honest outcome for a transition we failed to attribute.
  if (open) endPlay(null);

  const src = SOURCES.has(source) ? source : undefined;
  const pb = PLAYBACKS.has(playback) ? playback : undefined;
  open = { id, source: src, playback: pb, startedAt: Date.now(), listenedMs: 0, playingSince: null };
  emit({ type: 'play_start', ...id, ...(src ? { source: src } : null), ...(pb ? { playback: pb } : null) });
  saveOpen();
}

/** The media layer fell back (Spotify -> 30 s preview): record what really played. */
export function setPlayback(playback) {
  if (open && PLAYBACKS.has(playback)) { open.playback = playback; saveOpen(); }
}

/**
 * Audible-time clock. The media layer calls this on every state emit; only
 * a change of playing-ness does anything. Time accrues between true and
 * false, so a pause, a buffering stall or an internal switch stops it.
 */
export function setPlaying(playing) {
  if (!open) return;
  const now = Date.now();
  if (playing && !open.playingSince) {
    open.playingSince = now;
  } else if (!playing && open.playingSince) {
    open.listenedMs += now - open.playingSince;
    open.playingSince = null;
    saveOpen();
  }
}

/** User pressed pause / play (UI, keyboard or media key). */
export function notePause() {
  if (!open) return;
  const t = clock() || {};
  emit({ type: 'pause', ...open.id, ...(Number.isFinite(t.position_ms) ? { position_ms: t.position_ms } : null) });
}
export function noteResume() {
  if (!open) return;
  const t = clock() || {};
  emit({ type: 'resume', ...open.id, ...(Number.isFinite(t.position_ms) ? { position_ms: t.position_ms } : null) });
}

/** User scrubbed from `fromMs` to `toMs`. */
export function noteSeek(fromMs, toMs) {
  if (!open || !Number.isFinite(fromMs) || !Number.isFinite(toMs)) return;
  emit({ type: 'seek', ...open.id, data: { from_ms: Math.round(fromMs), to_ms: Math.round(toMs) } });
  saveOpen();
}

/** Something happened that is not about one track (search, vibe, DJ toggle). */
export function logEvent(type, fields = {}) {
  install();
  emit({ type, ...fields });
}

/** A track-scoped event that is not a play (queue_add). */
export function logTrackEvent(type, track, fields = {}) {
  install();
  const id = identify(track);
  if (id) emit({ type, ...id, ...fields });
}

/**
 * The open play stopped. `reason` is 'completed' | 'skipped' | 'replaced' |
 * 'abandoned', or null when it is genuinely unknown. `trigger` says how a
 * human ended it (next_button, media_key, prev, search, pick, queue_jump,
 * vibe_change). No-op when nothing is open, which is what makes every caller
 * safe to call twice.
 */
export function endPlay(reason, { trigger } = {}) {
  const play = open;
  if (!play) return;
  open = null;
  const heard = Math.round(heardSoFar(play));
  try { localStorage.removeItem(openKey()); } catch { /* storage unavailable */ }

  let position = null;
  let duration = null;
  try {
    const t = clock() || {};
    position = Number.isFinite(t.position_ms) ? t.position_ms : null;
    duration = Number.isFinite(t.duration_ms) && t.duration_ms > 0 ? t.duration_ms : null;
  } catch {
    /* the media layer is mid-teardown; send what we have */
  }
  // Wall-clock fallback so a source with no readable playhead (Spotify
  // mid-handoff, a video that never loaded) still reports elapsed time.
  const elapsed = Math.max(0, Date.now() - play.startedAt);
  if (position == null) position = elapsed;
  if (duration != null) position = Math.min(position, duration);
  /*
   * A completed play played all of it, by definition. This is not cosmetic:
   * spotify.js infers 'ended' from a playing -> paused-at-0 transition, so the
   * SDK's own playhead reads ZERO at exactly the moment we ask.
   *
   * The `duration != null` guard used to defeat this entirely. At that moment
   * the SDK gives position 0 AND no duration, so neither this line nor the
   * wall-clock fallback above fired — 0 is finite, so `position` was never
   * null — and EVERY completed play recorded 0 ms. Verified against real
   * local events: 10 of 10 completions had position_ms = 0, duration_ms NULL,
   * so total_played_ms was identically zero for everything a user listened to
   * all the way through. Only skips carried real numbers.
   *
   * With no duration, elapsed wall-clock is the best estimate available. It
   * over-reports when a track sat paused, which is the right way to be wrong
   * here: the alternative was recording nothing at all.
   */
  if (reason === 'completed') {
    position = duration != null ? duration : Math.max(position, elapsed);
  }

  emit({
    type: 'play_end',
    ...play.id,
    position_ms: Math.round(position),
    ...(duration != null ? { duration_ms: Math.round(duration) } : null),
    ...(reason ? { reason } : null),
    ...(reason !== 'completed' && trigger ? { trigger } : null),
    listened_ms: heard,
    ...(play.playback ? { playback: play.playback } : null),
    ...(play.source ? { source: play.source } : null),
  });
}
