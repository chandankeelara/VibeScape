/**
 * Spotify Web Playback SDK. Ported from frontend/app.js:3300-3560.
 *
 * CRITICAL — read src/media/README.md:
 *   - `window.onSpotifyWebPlaybackSDKReady` is a ONE-SHOT global. Assigned at
 *     module scope, never from a component.
 *   - Runs in mobile browsers too (Chrome/Firefox/Safari/Edge on Android and
 *     iOS, out of beta since 2021) — NOT desktop-only. Premium required;
 *     mobile-only Premium tiers are excluded.
 *
 * Background / lock-screen behaviour, which is imposed by the BROWSER, not by
 * the SDK:
 *   - Android browsers: background playback works.
 *   - iOS: backgrounding does not work, and music stops when the device locks.
 *   - Spotify's advice for real background playback is their native iOS /
 *     Android SDKs, which do exist — a Capacitor or native shell is the route
 *     if lock-screen full-track playback on iOS ever becomes a requirement.
 *
 * Only Premium accounts get a playback device. Everything here degrades to
 * no-ops when there's no token or no device, which is the common case.
 */

const sp = {
  sdkRequested: false,
  player: null,
  deviceId: null,
  token: null,
  isPremium: false,
  ready: false,
  lastState: null,
  positionMs: 0,
  durationMs: 0,
  positionAt: 0, // performance.now() when positionMs was captured
  pollTimer: null,
};

let onState = () => {};
let onReady = () => {};
let onEnded = () => {};
let onError = () => {};

export const setOnState = (fn) => { onState = fn; };
/** Fired when a Premium device appears — player.js upgrades a preview to full. */
export const setOnReady = (fn) => { onReady = fn; };
export const setOnEnded = (fn) => { onEnded = fn; };
export const setOnError = (fn) => { onError = fn; };

export function setToken(token) {
  sp.token = token || null;
  if (sp.token && !sp.player) connect();
}

/**
 * SpotifyAuthContext's getValidToken({ force }), registered once. Everything
 * here that talks to Spotify asks it first, so the token is refreshed at the
 * moment it is used — a new song, or the SDK's own periodic request — and
 * never goes stale behind a long session.
 */
let tokenProvider = null;
export function setTokenProvider(fn) { tokenProvider = fn || null; }

async function freshToken({ force = false } = {}) {
  if (tokenProvider) {
    try {
      const t = await tokenProvider({ force });
      if (t) sp.token = t;
      return t || null;
    } catch { /* provider trouble — fall back to what we hold */ }
  }
  return sp.token;
}

/** Premium flag from the /v1/me profile — set by SpotifyAuthContext. */
export function setPremium(v) { sp.isPremium = !!v; }

export function init() {
  if (sp.sdkRequested) return;
  sp.sdkRequested = true;
  // One-shot global — must exist before the SDK script finishes loading.
  window.onSpotifyWebPlaybackSDKReady = () => {
    sp.ready = true;
    if (sp.token) connect();
  };
}

function connect() {
  if (!sp.ready || !window.Spotify || sp.player || !sp.token) return;
  try {
    const player = new window.Spotify.Player({
      name: 'VibeScape',
      // The SDK calls this on connect and again whenever its token is about
      // to lapse (roughly hourly) — including mid-song and after a long pause.
      getOAuthToken: (cb) => { freshToken().then((t) => cb(t || '')); },
      volume: 0.8,
    });

    player.addListener('ready', ({ device_id }) => {
      sp.deviceId = device_id;
      // Declare ourselves the active playback target, or /player/play 404s.
      transferPlayback(device_id);
      onReady();
    });
    player.addListener('not_ready', () => { sp.deviceId = null; });

    player.addListener('account_error', () => {
      // Free account — a device will never appear.
      sp.isPremium = false;
      onError({ kind: 'account', message: 'Spotify Premium required for full-track playback.' });
    });
    player.addListener('authentication_error', () => {
      onError({ kind: 'auth', message: 'Spotify auth expired. Sign in again.' });
    });
    player.addListener('initialization_error', ({ message }) => {
      onError({ kind: 'init', message });
    });
    player.addListener('playback_error', ({ message }) => {
      onError({ kind: 'playback', message: message || 'unknown' });
    });

    player.addListener('player_state_changed', handleStateChange);

    player.connect();
    sp.player = player;
  } catch (e) {
    console.warn('[VibeScape] Spotify player connect failed:', e);
  }
}

function handleStateChange(playerState) {
  if (!playerState) {
    sp.lastState = null;
    stopPolling();
    emit();
    return;
  }

  // Capture the PREVIOUS state before overwriting — end-of-track is detected
  // as a transition, not from the new state alone.
  const prev = sp.lastState;
  sp.lastState = playerState;
  sp.positionMs = playerState.position || 0;
  sp.durationMs = playerState.duration || sp.durationMs;
  sp.positionAt = performance.now();

  if (playerState.paused) stopPolling();
  else startPolling();

  emit();

  // End of track: we WERE playing with a real duration and non-zero position,
  // and are now paused at exactly 0. The SDK emits no 'ended' event.
  const wasPlaying = !!(prev && !prev.paused && (prev.position || 0) > 0 && (prev.duration || 0) > 0);
  const endedNow =
    playerState.paused && (playerState.position || 0) === 0 && (playerState.duration || 0) > 0;
  if (wasPlaying && endedNow) onEnded();
}

function emit() {
  onState({
    playing: !!(sp.lastState && !sp.lastState.paused),
    position: sp.positionMs / 1000,
    duration: sp.durationMs / 1000,
  });
}

/**
 * The SDK fires player_state_changed only on actual state CHANGES — not
 * continuously. Without extrapolating from the last known position the
 * progress bar freezes between events. Legacy app.js:3429.
 */
function startPolling() {
  stopPolling();
  sp.pollTimer = setInterval(() => {
    if (!sp.lastState || sp.lastState.paused) return;
    const elapsed = performance.now() - sp.positionAt;
    sp.positionMs = Math.min(sp.durationMs, (sp.lastState.position || 0) + elapsed);
    emit();
  }, 250);
}

function stopPolling() {
  if (sp.pollTimer) clearInterval(sp.pollTimer);
  sp.pollTimer = null;
}

/** PUT /me/player — make our SDK device the active playback target. */
async function transferPlayback(deviceId) {
  if (!deviceId || !sp.token) return false;
  try {
    const tok = await freshToken();
    if (!tok) return false;
    const r = await fetch('https://api.spotify.com/v1/me/player', {
      method: 'PUT',
      headers: { Authorization: `Bearer ${tok}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_ids: [deviceId], play: false }),
    });
    // 204 transferred, 202 accepted (queued) — both fine.
    return r.ok || r.status === 204 || r.status === 202;
  } catch {
    return false;
  }
}

/** Mirrors legacy sdkActive() (app.js:1123) — all four conditions. */
export const isActive = () => !!(sp.token && sp.isPremium && sp.player && sp.deviceId);

const doPlay = (spotifyId, tok) =>
  fetch(`https://api.spotify.com/v1/me/player/play?device_id=${sp.deviceId}`, {
    method: 'PUT',
    headers: { Authorization: `Bearer ${tok}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ uris: [`spotify:track:${spotifyId}`] }),
  });

/**
 * Returns false when playback could not be taken over, so the caller can fall
 * back to the 30s preview instead of leaving the user with silence.
 */
export async function playTrack(spotifyId) {
  if (!isActive()) return false;
  try {
    // Every new song asks for a token first — this is where a long session
    // gets its refresh.
    let tok = await freshToken();
    if (!tok) return false;
    let r = await doPlay(spotifyId, tok);

    // 401 = the token died early (revoked elsewhere, clock skew). Force one
    // refresh and retry once.
    if (r.status === 401) {
      tok = await freshToken({ force: true });
      if (!tok) return false;
      r = await doPlay(spotifyId, tok);
    }

    // 404 = another Spotify client stole the active-device slot. Re-transfer
    // and retry once; the delay lets Spotify propagate it server-side.
    if (r.status === 404) {
      if (await transferPlayback(sp.deviceId)) {
        await new Promise((res) => setTimeout(res, 300));
        r = await doPlay(spotifyId, tok);
      }
    }
    if (r.status === 404) {
      onError({
        kind: 'device_lost',
        message:
          "Spotify playback couldn't take over. Pause Spotify on other devices, then try again.",
      });
      return false;
    }
    return r.ok || r.status === 204;
  } catch {
    return false;
  }
}

export function pause() { try { sp.player?.pause(); } catch { /* not connected */ } }
export function resume() { try { sp.player?.resume(); } catch { /* not connected */ } }

export function seek(frac) {
  if (!sp.player || !sp.durationMs) return;
  const ms = Math.round(sp.durationMs * frac);
  try {
    sp.player.seek(ms);
    // Re-anchor so the extrapolator doesn't snap back to the pre-seek value.
    sp.positionMs = ms;
    sp.positionAt = performance.now();
    if (sp.lastState) sp.lastState = { ...sp.lastState, position: ms };
    emit();
  } catch { /* not connected */ }
}

export function disconnect() {
  stopPolling();
  try { sp.player?.disconnect(); } catch { /* already gone */ }
  sp.player = null;
  sp.deviceId = null;
  sp.lastState = null;
}

export const getDeviceId = () => sp.deviceId;

/** Length of what the SDK is playing, in seconds (0 before the first state). */
export const getDuration = () => sp.durationMs / 1000;

/**
 * Live playhead in seconds.
 *
 * Reads the same extrapolated value the progress bar uses rather than the
 * raw last-known SDK position, which only updates on state CHANGES and would
 * sit frozen between them.
 *
 * Needed because during Spotify playback the <audio> element is detached, so
 * a caller reading `audioEl.currentTime` for "where are we" gets 0.
 */
export const getPosition = () => sp.positionMs / 1000;
