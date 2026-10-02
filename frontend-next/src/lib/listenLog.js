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
 *   (null)     pagehide with a play still open. The contract has no vocabulary
 *              for "the user closed the tab", and a wrong label is worse than
 *              a null: the backend counts a null-reason play_end in end_count
 *              and total_played_ms but puts it in no bucket, which is exactly
 *              right.
 *
 * A reload of the SAME track is not an end. player.loadTrack() runs again when
 * a Spotify device appears mid-preview and when leaving video mode; both would
 * otherwise bill one listen as a skip plus a fresh play.
 */

import { enqueue, flush } from './events';

/** Open play, or null. The single source of exactly-once. */
let open = null;

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
  // Close the open play before the page goes, so a user who leaves mid-track
  // still contributes the thing we most want to know — how far they got.
  // Not on visibilitychange: a hidden tab keeps playing, and ending the play
  // there would invent a stop that never happened.
  window.addEventListener('pagehide', () => {
    endPlay(null);
    flush({ unload: true });
  });
}

/** Identity the backend can resolve: spotify_id, else the internal tracks.id. */
function identify(track) {
  const sid = typeof track?.spotify_id === 'string' ? track.spotify_id.trim() : '';
  if (sid) return { spotify_id: sid };
  const n = Number(track?.id);
  return Number.isFinite(n) && n > 0 ? { track_id: n } : null;
}

function emit(fields) {
  const { vibe, vibe_source: vibeSource, dj_mode: djMode } = context() || {};
  const ev = { ...fields, client_ts: Date.now() };
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

const SOURCES = new Set(['queue', 'dj', 'search', 'autoplay']);

/**
 * A track started playing. `source` is where the pick came from
 * ('queue' | 'dj' | 'search' | 'autoplay'); anything else is dropped rather
 * than sent as a value the contract doesn't define.
 */
export function startPlay(track, { source } = {}) {
  install();
  const id = identify(track);
  if (!id) return; // un-ingested Spotify result — the backend cannot resolve it
  // Defensive only; every caller ends first. Without a reason it lands in no
  // bucket, which is the honest outcome for a transition we failed to attribute.
  if (open) endPlay(null);

  const src = SOURCES.has(source) ? source : undefined;
  open = { id, source: src, startedAt: Date.now() };
  emit({ type: 'play_start', ...id, ...(src ? { source: src } : null) });
}

/**
 * The open play stopped. `reason` is 'completed' | 'skipped' | 'replaced', or
 * null when it is genuinely unknown. No-op when nothing is open, which is what
 * makes every caller safe to call twice.
 */
export function endPlay(reason) {
  const play = open;
  if (!play) return;
  open = null;

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
    ...(play.source ? { source: play.source } : null),
  });
}
