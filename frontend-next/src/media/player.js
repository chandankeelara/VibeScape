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
import { getToken } from '../lib/session';

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
  onVideoError: () => {},
  onNeedsPremium: () => {},
  onSpotifyError: () => {},
};
export const setHooks = (h) => { hooks = { ...hooks, ...h }; };

function emit() {
  const snapshot = { playing: state.playing, source: state.source, mode: state.mode, track: state.track };
  listeners.forEach((fn) => fn(snapshot));
}
function emitTime(position, duration) {
  const t = { position, duration };
  timeListeners.forEach((fn) => fn(t));
}

export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function subscribeTime(fn) { timeListeners.add(fn); return () => timeListeners.delete(fn); }
export const getState = () => ({ ...state });

/* ------------------------------------------------------------------- init */

/** Call ONCE at startup, before React renders. Never from a component effect. */
export function init() {
  if (state.initialized) return;
  state.initialized = true;

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
  el.addEventListener('ended', () => { state.playing = false; glow.stop(); emit(); hooks.onEnded(); });
  el.addEventListener('timeupdate', () => emitTime(el.currentTime || 0, el.duration || 0));
  el.addEventListener('loadedmetadata', () => emitTime(el.currentTime || 0, el.duration || 0));

  youtube.setHandlers({
    onPlaying: () => { state.playing = true; emit(); setSessionState(true); },
    onPaused: () => { state.playing = false; emit(); setSessionState(false); },
    onEnded: () => { state.playing = false; emit(); hooks.onEnded(); },
    onError: (info) => hooks.onVideoError(info),
    onTime: ({ position, duration }) => emitTime(position, duration),
  });

  // Loads the IFrame API script and assigns the one-shot
  // window.onYouTubeIframeAPIReady global. Without this the video
  // stage never gets a player and the mode toggle is a dead switch.
  youtube.init();
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

export function loadTrack(track, { mode = state.mode } = {}) {
  if (!track) return;
  state.track = track;
  state.mode = mode;
  updateSessionMetadata(track);

  if (mode === 'video') {
    quietAudio();
    spotify.pause();
    state.source = null;
    emit();
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
    state.source = null;
    state.playing = false;
    emit();
    hooks.onNeedsPremium(track);
    return;
  }

  if (useSpotify) {
    quietAudio();
    state.source = 'spotify';
    // The SDK stream can't be tapped by AudioContext — glow goes static.
    glow.stop();
    glow.setAlpha(0.65);
    emit();
    spotify.playTrack(track.spotify_id).then((ok) => {
      // Couldn't take over the device — better a 30s preview than silence.
      if (!ok && state.track === track) {
        state.source = 'preview';
        emit();
        setPreviewSource(track);
        state.audioEl.play().catch(() => {});
      }
    });
    return;
  }

  state.source = 'preview';
  emit();
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
  } else {
    youtube.stop();
    if (track) loadTrack(track, { mode: 'audio' });
  }
  emit();
}

export function stop() {
  quietAudio();
  spotify.pause();
  youtube.stop();
  glow.stop();
  glow.setAlpha(0.65);
  state.playing = false;
  state.source = null;
  emit();
  emitTime(0, 0);
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

function applySessionHandlers() {
  if (!('mediaSession' in navigator)) return;
  const set = (action, fn) => { try { navigator.mediaSession.setActionHandler(action, fn); } catch {} };
  set('play', play);
  set('pause', pause);
  set('nexttrack', () => hooks.onEnded());
}
