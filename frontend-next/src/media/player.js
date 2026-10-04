/**
 * Playback facade — the ONLY thing React talks to.
 *
 * Owns the <audio> element and delegates to glow / youtube / spotify. Ported
 * from frontend/app.js (loadTrack :1185, stopPlayback :1322, setPreviewSource
 * :589, media session :2521-2600).
 *
 * Read src/media/README.md before changing anything here.
 *
 * Two subscription channels on purpose:
 *   subscribe()     → low-frequency state (playing, source). Feeds React state.
 *   subscribeTime() → ~4Hz position/duration. Feeds ONE leaf component.
 * Merging them would re-render the player tree several times a second.
 */

import * as glow from './glow';
import * as youtube from './youtube';
import * as spotify from './spotify';
import * as verify from './verify';
import { getToken } from '../lib/session';
import * as listenLog from '../lib/listenLog';

const listeners = new Set();
const timeListeners = new Set();

const state = {
  audioEl: null,
  track: null,
  playing: false,
  source: null, // 'spotify' | 'preview' | null
  mode: 'audio', // 'audio' | 'video'
  initialized: false,
};

/** Host callbacks (set by PlayerContext) for things only React can decide. */
let hooks = {
  onEnded: () => {},
  onNext: () => {},
  onPrevious: () => {},
  onVideoError: () => {},
  onNeedsPremium: () => {},
  onSpotifyError: () => {},
};
export const setHooks = (h) => { hooks = { ...hooks, ...h }; };

/*
 * Chrome's automatic picture-in-picture entry point.
 *
 * Kept OUT of `hooks` deliberately. Every other hook is set once by
 * PlayerContext in a single setHooks() call; this one is registered and
 * UNREGISTERED by the feature that owns the miniplayer window, because
 * registering the 'enterpictureinpicture' media-session action is itself what
 * makes the page eligible for auto-PiP (and what can make Chrome prompt for
 * the "automatic picture-in-picture" permission). A user who turned the
 * behaviour off must stop being eligible, not just ignore the callback.
 *
 * Setting it re-applies the handler set immediately, and reassertSession()
 * re-applies it again after a video starts — YouTube overwrites the media
 * session when it begins playing, which would otherwise drop this with it.
 */
let pipHandler = null;
export const setPipHandler = (fn) => {
  pipHandler = typeof fn === 'function' ? fn : null;
  applySessionHandlers();
};

function emit() {
  const snapshot = { playing: state.playing, source: state.source, mode: state.mode, track: state.track };
  listeners.forEach((fn) => fn(snapshot));
}
let lastPositionPush = 0;

function emitTime(position, duration) {
  const t = { position, duration };
  timeListeners.forEach((fn) => fn(t));
  // Throttled — the OS only needs it about once a second, and Safari is
  // unhappy about being hammered.
  const now = performance.now();
  if (now - lastPositionPush > 900) {
    lastPositionPush = now;
    setSessionPosition(position, duration);
  }
}

export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function subscribeTime(fn) { timeListeners.add(fn); return () => timeListeners.delete(fn); }
export const getState = () => ({ ...state });

/* ------------------------------------------------------------------- init */

/** Call ONCE at startup, before React renders. Never from a component effect. */
export function init() {
  if (state.initialized) return;
  state.initialized = true;

  // Telemetry's playhead. Registered here because this module owns the clock
  // and lib/listenLog.js must not import back into the media layer.
  //
  // duration is the duration of WHAT IS PLAYING, not the catalogue track
  // length: a 30s preview reports 30000. position_ms and duration_ms have to
  // come off the same clock or their ratio is nonsense, and "the user let the
  // audio we had run to its end" is the signal we actually want.
  listenLog.setClock(() => ({
    position_ms: Math.round(currentPosition() * 1000),
    duration_ms: Math.round(currentDuration() * 1000),
  }));

  const el = document.createElement('audio');
  el.id = 'player';
  el.preload = 'metadata';
  // MUST be set before any src assignment. A cross-origin preview without this
  // taints the Web Audio graph, and because audio routes through
  // ctx.destination the symptom is silence rather than an error.
  el.crossOrigin = 'anonymous';
  el.volume = 0.8;
  document.body.appendChild(el);
  state.audioEl = el;

  el.addEventListener('play', () => { state.playing = true; glow.start(el); emit(); setSessionState(true); });
  el.addEventListener('pause', () => { state.playing = false; glow.stop(); emit(); setSessionState(false); });
  el.addEventListener('ended', () => {
    state.playing = false; glow.stop(); emit();
    // BEFORE hooks.onEnded(). That hook runs next(), which loads a
    // replacement through loadTrack() — and loadTrack's default attribution
    // is 'skipped'. Closing the play here is what keeps a track that
    // finished by itself from being logged as a skip.
    listenLog.endPlay('completed');
    hooks.onEnded();
  });
  el.addEventListener('timeupdate', () => emitTime(el.currentTime || 0, el.duration || 0));
  el.addEventListener('loadedmetadata', () => emitTime(el.currentTime || 0, el.duration || 0));

  youtube.setHandlers({
    onPlaying: () => { state.playing = true; emit(); setSessionState(true); reassertSession(); },
    onPaused: () => { state.playing = false; emit(); setSessionState(false); },
    onEnded: () => { state.playing = false; emit(); listenLog.endPlay('completed'); hooks.onEnded(); },
    onError: (info) => hooks.onVideoError(info),
    onTime: ({ position, duration }) => emitTime(position, duration),
  });

  // Loads the IFrame API script and assigns the one-shot
  // window.onYouTubeIframeAPIReady global. Without this the video
  // stage never gets a player and the mode toggle is a dead switch.
  youtube.init();

  // Owns its own isolated <audio>; see src/media/verify.js for why it must
  // never share the main element.
  verify.init();
  // Without this the progress bar is dead during Spotify playback — the
  // <audio> element is detached, so nothing else emits position.
  spotify.setOnState(({ playing, position, duration }) => {
    state.playing = playing;
    emit();
    setSessionState(playing);
    emitTime(position, duration);
  });

  // Signing in mid-session: a track already playing as a 30s preview should
  // switch to the full stream as soon as a Premium device appears.
  spotify.setOnReady(() => {
    if (state.mode === 'audio' && state.track) loadTrack(state.track);
  });

  // The SDK has no 'ended' event — spotify.js infers it from a
  // playing -> paused-at-0 transition.
  spotify.setOnEnded(() => {
    state.playing = false;
    emit();
    listenLog.endPlay('completed');
    hooks.onEnded();
  });

  spotify.setOnError((info) => hooks.onSpotifyError(info));

  spotify.init();
  applySessionHandlers();
}

/* ------------------------------------------------------------ audio source */

function streamUrl(track) {
  const key = encodeURIComponent(track.spotify_id || '');
  const t = getToken();
  return `/api/stream/${key}${t ? `?token=${encodeURIComponent(t)}` : ''}`;
}

/**
 * Prefer the CDN preview_url so we don't burn backend egress proxying audio we
 * already have upstream. Fall back ONCE to /api/stream on error (link rot,
 * CORS, network) so the locally-downloaded MP3 keeps the track playable.
 */
function setPreviewSource(track) {
  const el = state.audioEl;
  const backendSrc = streamUrl(track);
  if (!track.preview_url) {
    el.src = backendSrc;
    return;
  }
  const onError = () => {
    console.warn('[VibeScape] preview_url load failed; falling back to /api/stream');
    try { el.src = backendSrc; el.play().catch(() => {}); } catch {}
  };
  el.addEventListener('error', onError, { once: true });
  el.src = track.preview_url;
}

/* --------------------------------------------------------------- commands */

/**
 * `endReason` / `source` are telemetry only and never affect playback.
 *
 * endReason defaults to 'skipped' because that is what an open play being
 * displaced actually means: if the track had ended on its own, the 'ended'
 * handler above would already have closed it as 'completed', and endPlay()
 * below would find nothing to close. Only the genuinely programmatic callers
 * (player.stop(), the post-sync re-roll) pass 'replaced'.
 */
export function loadTrack(track, { mode = state.mode, endReason = 'skipped', source } = {}) {
  if (!track) return;

  // Re-loading the SAME track is a source switch, not an end: it happens when
  // a Spotify device appears mid-preview (setOnReady above) and when leaving
  // video mode. Billing that as a skip plus a fresh play would double the
  // play count and invent a skip the user never made.
  const continuing = listenLog.isOpen(track);
  if (!continuing) listenLog.endPlay(endReason);

  state.track = track;
  state.mode = mode;
  updateSessionMetadata(track);

  if (mode === 'video') {
    quietAudio();
    spotify.pause();
    // setMode() also starts this, but loading a NEW track while already in
    // video mode never goes through setMode — so without this the anchor
    // would be missing on every track change after the first.
    startSessionAnchor();
    state.source = null;
    emit();
    if (!continuing) listenLog.startPlay(track, { source });
    return; // the video feature resolves the id and calls cueVideo()
  }

  const metadataOnly = track.classification_source === 'metadata_only';
  // Ask the SDK directly. This used to be a `spotifyActive` argument that
  // no caller ever passed, so it silently defaulted to false and every
  // track fell through to the 30s preview even when signed in.
  const useSpotify = spotify.isActive() && !!track.spotify_id;

  if (!useSpotify && metadataOnly) {
    // No local audio — /api/stream would 404. Refusing with a clear message
    // beats a silent network failure.
    quietAudio();
    // Nothing is going to play, so do not hold a session hostage with silence.
    stopSessionAnchor();
    state.source = null;
    state.playing = false;
    emit();
    // No play_start: nothing is going to play, and a play the user never heard
    // would still be counted as one by user_track_stats.
    hooks.onNeedsPremium(track);
    return;
  }

  if (useSpotify) {
    quietAudio();
    // Spotify plays through the SDK's OWN iframe, so just like video mode the
    // page is left owning no playing media and the SDK takes the OS session.
    // Without this the media keys drive Spotify's session, not ours.
    startSessionAnchor();
    state.source = 'spotify';
    // The SDK stream can't be tapped by AudioContext — glow goes static.
    glow.stop();
    glow.setAlpha(0.65);
    emit();
    if (!continuing) listenLog.startPlay(track, { source });
    spotify.playTrack(track.spotify_id).then((ok) => {
      // Couldn't take over the device — better a 30s preview than silence.
      if (!ok && state.track === track) {
        // Real element is about to play, so it owns the session on its own.
        stopSessionAnchor();
        state.source = 'preview';
        emit();
        setPreviewSource(track);
        state.audioEl.play().catch(() => {});
      }
    });
    return;
  }

  // Preview mode needs no anchor: the real <audio> element is playing the
  // song, so the page already owns the session. A second element here would
  // only compete with it.
  stopSessionAnchor();
  state.source = 'preview';
  emit();
  if (!continuing) listenLog.startPlay(track, { source });
  setPreviewSource(track);
  state.audioEl.play().catch(() => { state.playing = false; emit(); });
}

/** Hand a resolved YouTube id to the iframe player (video mode). */
export function cueVideo(videoId) { youtube.cueOrPlay(videoId); }

export function play() {
  if (state.mode === 'video') return youtube.play();
  if (state.source === 'spotify') return spotify.resume();
  state.audioEl?.play().catch(() => {});
}

export function pause() {
  if (state.mode === 'video') return youtube.pause();
  if (state.source === 'spotify') return spotify.pause();
  state.audioEl?.pause();
}

export function seek(frac) {
  const f = Math.max(0, Math.min(1, frac));
  if (state.mode === 'video') {
    const d = youtube.getDuration();
    if (d > 0) youtube.seekTo(d * f);
    return;
  }
  if (state.source === 'spotify') return spotify.seek(f);
  const el = state.audioEl;
  if (el && Number.isFinite(el.duration)) el.currentTime = el.duration * f;
}

export function setMode(mode, track) {
  state.mode = mode;
  if (mode === 'video') {
    quietAudio();
    spotify.pause();
    // Must come AFTER quietAudio(): that is the call that drops our claim on
    // the OS media session and lets the YouTube iframe take it.
    startSessionAnchor();
  } else {
    stopSessionAnchor();
    youtube.stop();
    if (track) loadTrack(track, { mode: 'audio' });
  }
  emit();
}

export function stop() {
  // Teardown is programmatic — sign-out, an empty library. Not a skip.
  listenLog.endPlay('replaced');
  quietAudio();
  spotify.pause();
  youtube.stop();
  stopSessionAnchor();
  glow.stop();
  glow.setAlpha(0.65);
  state.playing = false;
  state.source = null;
  emit();
  emitTime(0, 0);
}

/* ------------------------------------------------------- verify support */

let verifySnapshot = null;

/**
 * Pause whatever is playing and remember enough to put it back exactly.
 * The verify clip plays on its own element, so the main one is left intact —
 * we only need its position and play state.
 */
export function snapshotAndPauseForVerify() {
  const usingSpotify = state.source === 'spotify';
  if (usingSpotify) {
    verifySnapshot = { source: 'spotify', playing: state.playing };
    spotify.pause();
  } else {
    const el = state.audioEl;
    verifySnapshot = {
      source: 'preview',
      playing: !!el && !el.paused,
      time: el && Number.isFinite(el.currentTime) ? el.currentTime : 0,
      src: el?.src || '',
    };
    try { el?.pause(); } catch { /* nothing playing */ }
  }
  glow.stop();
}

/** Restore what snapshotAndPauseForVerify() paused, if it was playing. */
export function restoreAfterVerify() {
  const snap = verifySnapshot;
  verifySnapshot = null;
  if (!snap || !snap.playing) return;

  if (snap.source === 'spotify') {
    spotify.resume();
    return;
  }
  const el = state.audioEl;
  if (!el) return;
  // The element kept its src; restore it only if something cleared it.
  if (!el.src && snap.src) el.src = snap.src;
  try {
    if (Number.isFinite(snap.time) && snap.time > 0) el.currentTime = snap.time;
  } catch { /* not seekable yet */ }
  el.play().catch(() => {});
}

/* ------------------------------------------------ media-session anchor */

/**
 * A silent, looping <audio> kept playing for as long as VIDEO mode is active,
 * purely so the page keeps owning the OS media session.
 *
 * The OS routes media keys to whichever browsing context most recently
 * started playing audio. setMode('video') calls quietAudio(), which pauses
 * our element and strips its src — so the page stopped owning any playing
 * media and the YouTube IFRAME became the only claimant. The OS panel then
 * showed the video's title and its next/prev did YouTube's thing, because a
 * cross-origin iframe's session cannot be overridden from here.
 *
 * Keeping this element playing means we still own a session, so our metadata
 * and our nexttrack/previoustrack handlers stay the ones the OS talks to.
 *
 * Starting it is safe under autoplay policy: entering video mode is a click.
 */
let anchorEl = null;

/** 8 kHz mono 8-bit PCM, 0.25s. Built here so no 2.7KB base64 blob lands in source. */
function silentWavUrl() {
  const rate = 8000;
  const frames = rate / 4;
  const buf = new Uint8Array(44 + frames);
  const view = new DataView(buf.buffer);
  const tag = (off, s) => { for (let i = 0; i < s.length; i++) buf[off + i] = s.charCodeAt(i); };
  tag(0, 'RIFF'); view.setUint32(4, 36 + frames, true); tag(8, 'WAVE');
  tag(12, 'fmt '); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate, true);
  view.setUint16(32, 1, true); view.setUint16(34, 8, true);
  tag(36, 'data'); view.setUint32(40, frames, true);
  // Silence in 8-bit PCM is 128, NOT 0 — it is unsigned and centred at mid-scale.
  // Filling with 0 would emit full-amplitude DC, which is audible as a thump.
  buf.fill(128, 44);
  return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
}

function startSessionAnchor() {
  if (!anchorEl) {
    anchorEl = document.createElement('audio');
    anchorEl.id = 'sessionAnchor';
    anchorEl.loop = true;
    anchorEl.preload = 'auto';
    // Deliberately NOT muted. A muted element does not count as playing media
    // for the Media Session API, which would defeat the entire purpose.
    anchorEl.volume = 1;
    anchorEl.src = silentWavUrl();
    document.body.appendChild(anchorEl);
  }
  anchorEl.play().catch(() => { /* blocked without a gesture; video mode has one */ });
}

function stopSessionAnchor() {
  try { anchorEl?.pause(); } catch { /* never started */ }
}

/**
 * Re-publish our metadata after the video starts.
 *
 * The YouTube iframe sets its own session when it begins playing, which can
 * overwrite what the OS panel shows even while we hold the session. Pushing
 * ours again afterwards puts the track's real title/artist/art back.
 */
function reassertSession() {
  updateSessionMetadata(state.track);
  applySessionHandlers();
}

function quietAudio() {
  const el = state.audioEl;
  if (!el) return;
  try { el.pause(); } catch {}
  el.removeAttribute('src');
  el.load();
}

/* ---------------------------------------------------------- media session */

function updateSessionMetadata(t) {
  if (!('mediaSession' in navigator) || !t) return;
  try {
    navigator.mediaSession.metadata = new window.MediaMetadata({
      title: t.title || 'Untitled',
      artist: t.artist || 'Unknown artist',
      album: t.album || '',
      artwork: t.artwork_url ? [{ src: t.artwork_url, sizes: '512x512', type: 'image/jpeg' }] : [],
    });
  } catch {}
}

function setSessionState(playing) {
  if (!('mediaSession' in navigator)) return;
  try { navigator.mediaSession.playbackState = playing ? 'playing' : 'paused'; } catch {}
}

/**
 * Tell the OS where we are in the track. This is what draws the scrubber on
 * the lock screen and in the notification shade; without it the controls show
 * but the position bar stays empty.
 */
function setSessionPosition(position, duration) {
  if (!('mediaSession' in navigator) || !navigator.mediaSession.setPositionState) return;
  if (!Number.isFinite(duration) || duration <= 0) return;
  try {
    navigator.mediaSession.setPositionState({
      duration,
      position: Math.min(Math.max(position, 0), duration),
      playbackRate: 1,
    });
  } catch { /* Safari throws on out-of-range values */ }
}

function applySessionHandlers() {
  if (!('mediaSession' in navigator)) return;
  const set = (action, fn) => {
    // Unsupported actions throw rather than no-op; an unset handler means the
    // OS simply doesn't offer that control.
    try { navigator.mediaSession.setActionHandler(action, fn); } catch { /* unsupported */ }
  };
  set('play', play);
  set('pause', pause);
  set('nexttrack', () => hooks.onNext());
  set('previoustrack', () => hooks.onPrevious());
  set('stop', stop);

  // Scrubbing from the lock screen / car head unit.
  set('seekto', (details) => {
    const d = currentDuration();
    if (d > 0 && Number.isFinite(details?.seekTime)) seek(details.seekTime / d);
  });
  set('seekbackward', (details) => seekBy(-(details?.seekOffset || 10)));
  set('seekforward', (details) => seekBy(details?.seekOffset || 10));

  // Chrome fires this with activation when the tab is occluded (and when the
  // user hits a browser-provided PiP control), which is the only way to open
  // a Document PiP window without a click. `null` unregisters, which is how
  // the app stops being eligible — see setPipHandler above.
  set('enterpictureinpicture', pipHandler ? (details) => pipHandler(details) : null);
}

function currentDuration() {
  if (state.mode === 'video') return youtube.getDuration();
  const el = state.audioEl;
  return el && Number.isFinite(el.duration) ? el.duration : 0;
}

function seekBy(deltaSeconds) {
  const d = currentDuration();
  if (d <= 0) return;
  seek((currentPosition() + deltaSeconds) / d);
}

/**
 * Live playhead for the active source.
 *
 * Video used to be hardcoded to 0 here, so every relative seek — the ±10s
 * media keys, a car head unit's skip — measured from the START of the video
 * instead of the playhead. Skipping forward from 2:30 landed you at 0:10.
 */
function currentPosition() {
  if (state.mode === 'video') return youtube.getCurrentTime();
  if (state.source === 'spotify') return spotify.getPosition();
  const el = state.audioEl;
  return el && Number.isFinite(el.currentTime) ? el.currentTime : 0;
}
