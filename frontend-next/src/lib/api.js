/**
 * The single API client for the React app.
 *
 * Rules (enforced by convention, see .claude/agents/frontend-owner.md):
 *   - No bare fetch() anywhere else in the app. Everything goes through here.
 *   - Auth is `Authorization: Bearer <session_token>`.
 *   - <audio>/<img> src URLs can't carry headers, so those use the ?token=
 *     query variant instead — see streamUrl(). Do NOT extend ?token= to any
 *     other endpoint; the backend only scrubs it from logs for /api/stream/*.
 *
 * Route list mirrors docs/mobile-api.md §7, whose source of truth is
 * backend/app.py.
 */

import { getToken, clearToken } from './session';

// Same-origin in production (FastAPI serves this app). In dev, Vite proxies
// /api through to localhost:8000, so a relative base works in both cases.
const BASE = '';

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

async function request(path, { method = 'GET', body, headers, auth = true, spotifyToken } = {}) {
  const h = { ...(headers || {}) };

  if (auth) {
    const t = getToken();
    if (!t) throw new ApiError('No session', 401, null);
    h.Authorization = `Bearer ${t}`;
  }
  if (spotifyToken) h['X-Spotify-Authorization'] = `Bearer ${spotifyToken}`;
  if (body !== undefined) h['Content-Type'] = 'application/json';

  const res = await fetch(BASE + path, {
    method,
    headers: h,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  // A 401 on an authed call means the session died server-side. Clear it so
  // the app falls back to the login flow rather than looping on failures.
  if (res.status === 401 && auth) {
    clearToken();
    throw new ApiError('Session expired', 401, null);
  }

  if (res.status === 204) return null;

  let payload = null;
  const text = await res.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!res.ok) {
    const detail = payload?.detail;
    const msg = detail?.error || detail || payload?.message || `HTTP ${res.status}`;
    throw new ApiError(typeof msg === 'string' ? msg : `HTTP ${res.status}`, res.status, payload);
  }
  return payload;
}

const qs = (params) => {
  const usable = Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== '');
  return usable.length ? `?${new URLSearchParams(usable)}` : '';
};

/* ---------------------------------------------------------------- public */

export const health = () => request('/api/health', { auth: false });
export const moods = () => request('/api/moods', { auth: false });
export const clientConfig = () => request('/api/client-config', { auth: false });
export const spotifyConfig = () => request('/api/spotify/config', { auth: false });
/**
 * Trade a SERVER-minted refresh token (from /api/auth/spotify-oauth) for a
 * fresh access token. The backend holds the client secret those need. PKCE
 * tokens never come here — SpotifyAuthContext refreshes them against Spotify.
 */
export const spotifyRefresh = (refresh_token) =>
  request('/api/spotify/refresh', { method: 'POST', body: { refresh_token } });
/**
 * WARNING: `GET /api/users` does NOT exist in backend/app.py. It is listed in
 * docs/mobile-api.md §7, but that doc is stale — verified 2026-09-27 against
 * the route table. Kept only so the profile-picker UI still compiles; it will
 * 404. Do not build new features on it.
 */
export const listUsers = () => request('/api/users', { auth: false });

/** Curated per-mood demo tracks for the landing page hero. */
export const demoMoods = () => request('/api/demo/moods', { auth: false });

/** Auth is email/password — NOT the user_id/PIN flow the legacy overlay used. */
export const signup = (payload) => request('/api/auth/signup', { method: 'POST', body: payload, auth: false });
export const login = (payload) => request('/api/auth/login', { method: 'POST', body: payload, auth: false });

/** Zero-friction demo mode — all guests share one 'Guest' user (app.py:703). */
export const guestLogin = () => request('/api/auth/guest', { method: 'POST', auth: false });

/** Log in / sign up with a Spotify authorization code (app.py:525). Returns
 *  the same payload shape as /api/auth/login. */
export const spotifyOAuthLogin = (payload) =>
  request('/api/auth/spotify-oauth', { method: 'POST', body: payload, auth: false });

/* ------------------------------------------------------------------ auth */

export const me = () => request('/api/auth/me');
export const logout = () => request('/api/auth/logout', { method: 'POST' });
export const linkSpotify = (payload) => request('/api/auth/spotify-link', { method: 'POST', body: payload });

/* ---------------------------------------------------------------- tracks */

export const tracks = (params) => request(`/api/tracks${qs(params)}`);
export const searchTracks = (params) => request(`/api/tracks/search${qs(params)}`);
export const randomTrack = (params) => request(`/api/tracks/random${qs(params)}`);
export const similarTracks = (trackKey, params) =>
  request(`/api/tracks/${encodeURIComponent(trackKey)}/similar${qs(params)}`);

/**
 * POST variant of /similar (backend/app.py:1506 → _similar_dj). DJ mode needs
 * this because the session weights don't fit in a query string.
 * body: { mode:'dj', positive_ids:[{id,weight}], negative_ids:[{id,weight}],
 *         exclude_ids:[int], limit:int }
 */
export const similarTracksDj = (trackKey, body) =>
  request(`/api/tracks/${encodeURIComponent(trackKey)}/similar`, { method: 'POST', body });
export const trackFeatures = (trackKey) =>
  request(`/api/tracks/${encodeURIComponent(trackKey)}/features`);
export const spotifyForApple = (appleId) =>
  request(`/api/track/${encodeURIComponent(appleId)}/spotify`);

/* --------------------------------------------------------------- youtube */

export const trackYoutube = (trackId) => request(`/api/tracks/${trackId}/youtube`);
export const searchTrackYoutube = (trackId, params) =>
  request(`/api/tracks/${trackId}/youtube/search${qs(params)}`);
export const setTrackYoutube = (trackId, youtubeId) =>
  request(`/api/tracks/${trackId}/youtube`, { method: 'POST', body: { youtube_id: youtubeId } });

/* -------------------------------------------------------------- playback */

/**
 * Media-element URLs, NOT a fetch. <audio>/<img> cannot send an Authorization
 * header, so the backend accepts ?token= on /api/stream/* specifically.
 */
export function streamUrl(trackKey, { spotify = false } = {}) {
  const t = getToken();
  const path = spotify
    ? `/api/stream/spotify/${encodeURIComponent(trackKey)}`
    : `/api/stream/${encodeURIComponent(trackKey)}`;
  return `${BASE}${path}${t ? `?token=${encodeURIComponent(t)}` : ''}`;
}

/* ------------------------------------------------------------ telemetry */

/**
 * Does this browser honour `fetch(..., { keepalive: true })`?
 *
 * Firefox ignored the flag silently until 133 (the request is simply cancelled
 * at unload), and the property getter is absent there, which is what this
 * probes. Cheap, done once, never throws.
 */
const KEEPALIVE_OK = (() => {
  try {
    return typeof Request === 'function' && 'keepalive' in new Request('/');
  } catch {
    return false;
  }
})();

/**
 * POST /api/events — listening telemetry. Deliberately NOT routed through
 * request(), for three reasons:
 *
 *   1. request() calls clearToken() on 401. A telemetry 401 must never sign
 *      the user out; this one treats every non-OK status as "stop sending"
 *      and leaves the session alone.
 *   2. It must never reject into a caller — it resolves with the Response and
 *      lets lib/events.js decide, and never throws for a non-2xx.
 *   3. The unload path needs `keepalive`.
 *
 * Why keepalive and not navigator.sendBeacon: /api/events is guarded by
 * require_user (backend/app.py), which is header-only Bearer — sendBeacon
 * cannot set a header, and ?token= is the controlled compromise for
 * /api/stream/* ONLY (see streamUrl above). keepalive is the one unload-safe
 * transport that can still send Authorization, so it is the primary path and
 * sendBeacon is not used at all. On a browser without keepalive the unload
 * flush degrades to a plain fetch that may be cancelled — the right failure
 * for telemetry.
 *
 * `token` is passed in rather than read here so a sign-out racing the final
 * flush still sends under the session that produced the events.
 */
export function postEvents(events, { unload = false, token } = {}) {
  const t = token || getToken();
  if (!t || !events || !events.length) return null;
  return fetch(`${BASE}/api/events`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${t}` },
    body: JSON.stringify({ events }),
    keepalive: unload && KEEPALIVE_OK,
  });
}

/* ---------------------------------------------------------------- ingest */

export const ingestSpotify = (payload, spotifyToken) =>
  request('/api/ingest/spotify', { method: 'POST', body: payload, spotifyToken });
export const ingestSpotifyPublic = (payload, spotifyToken) =>
  request('/api/ingest/spotify-public', { method: 'POST', body: payload, spotifyToken });
export const ingestSingle = (payload, spotifyToken) =>
  request('/api/ingest/single', { method: 'POST', body: payload, spotifyToken });
export const ingestStatus = (jobId) => request(`/api/ingest/status/${encodeURIComponent(jobId)}`);
export const cancelIngest = (jobId) =>
  request(`/api/ingest/status/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
export const clearIngest = () => request('/api/ingest/clear', { method: 'POST' });

/* ---------------------------------------------- spotify catalog (proxied) */

export const spotifyLibrary = (params, spotifyToken) =>
  request(`/api/spotify/library${qs(params)}`, { spotifyToken });
export const spotifySearch = (params, spotifyToken) =>
  request(`/api/spotify/search${qs(params)}`, { spotifyToken });

/* ----------------------------------------------------------------- admin */

export const adminUsers = () => request('/api/admin/users');
export const adminUserStats = (userId) => request(`/api/admin/users/${userId}/stats`);
export const adminUserTracks = (userId, params) => request(`/api/admin/users/${userId}/tracks${qs(params)}`);
export const adminDeleteUser = (userId) => request(`/api/admin/users/${userId}`, { method: 'DELETE' });
