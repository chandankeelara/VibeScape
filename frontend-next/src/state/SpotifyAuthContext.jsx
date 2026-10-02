/**
 * Spotify OAuth (Authorization Code + PKCE). Ported from
 * frontend/app.js:2762-3260.
 *
 * Owns the access token and pushes it into the media layer. Search, the sync
 * modal, and the debug panel all consume it through useSpotifyAuth().
 *
 * Storage keys are namespaced per VibeScape user (`spotify_{uid}_*`) exactly
 * as the legacy app does — two profiles on one device must not share a Spotify
 * token.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import * as api from '../lib/api';
import * as spotifyMedia from '../media/spotify';
import { useToast } from './ToastContext';

const SpotifyCtx = createContext(null);

const PKCE_PENDING = 'vibescape_spotify_pkce_pending';

const SCOPE =
  'streaming user-read-email user-read-private user-library-read ' +
  'playlist-read-private playlist-read-collaborative user-top-read playlist-modify-private';

// Bump when SCOPE changes — forces a re-consent instead of silently failing
// on an endpoint the old token lacks permission for.
const SCOPE_VERSION = 3;

const keysFor = (uid = '_anon') => ({
  verifier: `spotify_${uid}_pkce_verifier`,
  token: `spotify_${uid}_access_token`,
  refresh: `spotify_${uid}_refresh_token`,
  expiry: `spotify_${uid}_token_expiry`,
  profile: `spotify_${uid}_profile`,
  scopeVersion: `spotify_${uid}_scope_version`,
});

/**
 * Persist tokens that the BACKEND already minted, under the namespace this
 * provider will read on mount.
 *
 * `POST /api/auth/spotify-oauth` performs the token exchange server-side and
 * returns spotify_access_token / _refresh_token / _expires_in alongside the
 * session (backend/app.py:606). So signing in with Spotify ALREADY yields a
 * streaming token — there is no reason to make the user consent a second time
 * just to connect.
 *
 * Called from AuthContext.completeLogin, which runs while this provider is
 * still unmounted (it lives below AuthGate). Writing to storage rather than
 * calling adoptTokens() is what bridges that gap: the provider's restore
 * effect picks them up when it mounts with the resolved userId.
 */
export function persistTokensFor(userId, payload) {
  if (!payload?.spotify_access_token) return;
  const k = keysFor(userId ?? '_anon');
  const expiresAt = Date.now() + (payload.spotify_expires_in || 3600) * 1000;
  try {
    localStorage.setItem(k.token, payload.spotify_access_token);
    if (payload.spotify_refresh_token) localStorage.setItem(k.refresh, payload.spotify_refresh_token);
    localStorage.setItem(k.expiry, String(expiresAt));
    // The login scope string is kept identical to SCOPE below, so the stored
    // version is honest and the restore path won't force a re-consent.
    localStorage.setItem(k.scopeVersion, String(SCOPE_VERSION));
    // Synthesize the /v1/me shape from what the login response already told
    // us, so isPremium is correct on the first render — no extra round-trip.
    localStorage.setItem(k.profile, JSON.stringify({
      id: payload.spotify_user_id ?? null,
      display_name: payload.spotify_display_name ?? null,
      email: payload.spotify_email ?? null,
      country: payload.spotify_country ?? null,
      product: payload.spotify_product ?? null,
      images: payload.avatar_url ? [{ url: payload.avatar_url }] : [],
    }));
  } catch { /* private mode — falls back to the connect button */ }
}

/* ------------------------------------------------------------ PKCE helpers */

function base64UrlEncode(bytes) {
  let str = '';
  const arr = new Uint8Array(bytes);
  for (let i = 0; i < arr.length; i++) str += String.fromCharCode(arr[i]);
  return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function randomString(len) {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~';
  const buf = new Uint8Array(len);
  crypto.getRandomValues(buf);
  return Array.from(buf, (b) => chars[b % chars.length]).join('');
}

const sha256 = (input) => crypto.subtle.digest('SHA-256', new TextEncoder().encode(input));

/* -------------------------------------------------------------- provider */

export function SpotifyAuthProvider({ userId, children }) {
  const toast = useToast();
  const [config, setConfig] = useState({ clientId: '', redirectUri: '' });
  const [token, setTokenState] = useState('');
  const [profile, setProfile] = useState(null);
  const [expiresAt, setExpiresAt] = useState(null);

  const keys = useMemo(() => keysFor(userId ?? '_anon'), [userId]);

  useEffect(() => {
    api.spotifyConfig()
      .then((j) => setConfig({ clientId: j.client_id || '', redirectUri: j.redirect_uri || '' }))
      .catch(() => {/* connection issues surface via health check */});
  }, []);

  const clear = useCallback(() => {
    Object.values(keys).forEach((k) => { try { localStorage.removeItem(k); } catch {} });
    setTokenState('');
    setProfile(null);
    setExpiresAt(null);
    spotifyMedia.setToken(null);
    spotifyMedia.disconnect();
  }, [keys]);

  /* restore on mount / user change */
  useEffect(() => {
    let tok = '', exp = 0, storedVersion = 0, prof = null;
    try {
      tok = localStorage.getItem(keys.token) || '';
      exp = parseInt(localStorage.getItem(keys.expiry) || '0', 10);
      storedVersion = parseInt(localStorage.getItem(keys.scopeVersion) || '0', 10);
      const raw = localStorage.getItem(keys.profile);
      if (raw) prof = JSON.parse(raw);
    } catch { /* storage unavailable */ }

    if (!tok || !exp) return;

    if (!storedVersion || storedVersion < SCOPE_VERSION) {
      clear();
      toast('Spotify sign-in refresh needed to enable playlist link import.', 'info');
      return;
    }
    if (Date.now() >= exp) { clear(); return; }

    setTokenState(tok);
    setProfile(prof);
    setExpiresAt(exp);
    spotifyMedia.setToken(tok);
  }, [keys, clear, toast]);

  const persist = useCallback((accessToken, refreshToken, expiresIn) => {
    const expiresAt = Date.now() + (expiresIn || 3600) * 1000;
    try {
      localStorage.setItem(keys.token, accessToken);
      if (refreshToken) localStorage.setItem(keys.refresh, refreshToken);
      localStorage.setItem(keys.expiry, String(expiresAt));
      localStorage.setItem(keys.scopeVersion, String(SCOPE_VERSION));
      localStorage.removeItem(keys.verifier);
    } catch {}
    setTokenState(accessToken);
    setExpiresAt(expiresAt);
    spotifyMedia.setToken(accessToken);
  }, [keys]);

  /** Kick off the PKCE redirect. */
  const signIn = useCallback(async () => {
    if (!config.clientId) {
      toast('Spotify not configured — set SPOTIFY_CLIENT_ID on the backend.', 'warning');
      return;
    }
    const verifier = randomString(96);
    // Marks this redirect as OURS. The login flow (features/login/useSpotifyLogin)
    // also returns with ?code=, but that code is redeemed SERVER-SIDE by
    // /api/auth/spotify-oauth. Redeeming it here too would burn the
    // single-use code and fail with invalid_grant — so the callback handler
    // only acts when this marker is present.
    try { sessionStorage.setItem(PKCE_PENDING, '1'); } catch {}
    const challenge = base64UrlEncode(await sha256(verifier));
    try { localStorage.setItem(keys.verifier, verifier); } catch {}

    const params = new URLSearchParams({
      response_type: 'code',
      client_id: config.clientId,
      redirect_uri: config.redirectUri,
      code_challenge_method: 'S256',
      code_challenge: challenge,
      scope: SCOPE,
      // The /callback bridge routes the return leg by `state`, mapping known
      // markers to fixed paths (backend/app.py:1686). An unrecognised value
      // falls through to the same default today, so a random one happened to
      // work — but it worked by accident, and the bridge's table is where a
      // second client would be routed. Declare the marker the app actually
      // is. CSRF protection here is PKCE, not state.
      state: 'vs_next',
    });
    window.location.href = `https://accounts.spotify.com/authorize?${params}`;
  }, [config, keys, toast]);

  /** Exchange ?code= on return from Spotify. */
  const exchangeCode = useCallback(async (code) => {
    let verifier = '';
    try { verifier = localStorage.getItem(keys.verifier) || ''; } catch {}
    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      code,
      redirect_uri: config.redirectUri,
      client_id: config.clientId,
      code_verifier: verifier,
    });
    try {
      const r = await fetch('https://accounts.spotify.com/api/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
      });
      if (!r.ok) {
        toast(`Spotify token exchange failed (${r.status}).`, 'error');
        return false;
      }
      const j = await r.json();
      persist(j.access_token || '', j.refresh_token || '', j.expires_in);

      // Fetch profile + link the Spotify identity to the VibeScape user.
      try {
        const pr = await fetch('https://api.spotify.com/v1/me', {
          headers: { Authorization: `Bearer ${j.access_token}` },
        });
        if (pr.ok) {
          const p = await pr.json();
          setProfile(p);
          try { localStorage.setItem(keys.profile, JSON.stringify(p)); } catch {}
          api.linkSpotify({ spotify_user_id: p.id, spotify_email: p.email }).catch(() => {});
        }
      } catch {}
      return true;
    } catch {
      toast('Spotify token exchange error.', 'error');
      return false;
    }
  }, [config, keys, persist, toast]);

  /**
   * Adopt tokens minted SERVER-SIDE by /api/auth/spotify-oauth, so a user who
   * signed in with Spotify also gets an in-app streaming token instead of
   * having to press "connect" again. Called by the login feature.
   */
  const adoptTokens = useCallback(({ access_token, refresh_token, expires_in }) => {
    if (!access_token) return;
    persist(access_token, refresh_token || '', expires_in);
    fetch('https://api.spotify.com/v1/me', { headers: { Authorization: `Bearer ${access_token}` } })
      .then((r) => (r.ok ? r.json() : null))
      .then((p) => {
        if (!p) return;
        setProfile(p);
        try { localStorage.setItem(keys.profile, JSON.stringify(p)); } catch {}
      })
      .catch(() => {});
  }, [persist, keys]);

  /** True only when THIS provider started the redirect. */
  const consumePkcePending = useCallback(() => {
    try {
      const v = sessionStorage.getItem(PKCE_PENDING);
      sessionStorage.removeItem(PKCE_PENDING);
      return v === '1';
    } catch { return false; }
  }, []);

  // Keep the media layer's premium flag in sync — it gates full-track
  // playback vs the 30s preview.
  useEffect(() => {
    spotifyMedia.setPremium(profile?.product === 'premium');
  }, [profile]);

  // Disconnect the SDK when this provider goes away — sign-out, or a switch
  // to a different VibeScape user. Leaving it connected would keep a Premium
  // device registered against the previous account's token.
  useEffect(() => () => {
    spotifyMedia.setToken(null);
    spotifyMedia.disconnect();
  }, []);

  const value = useMemo(
    () => ({
      token,
      profile,
      config,
      // Exposed for the debug panel. Consumers must NOT re-derive SCOPE —
      // a second copy would drift, which is the bug SCOPE_VERSION exists
      // to catch.
      scope: SCOPE,
      scopeVersion: SCOPE_VERSION,
      expiresAt,
      isConnected: !!token,
      isPremium: profile?.product === 'premium',
      signIn,
      signOut: clear,
      exchangeCode,
      adoptTokens,
      consumePkcePending,
    }),
    [token, profile, config, expiresAt, signIn, clear, exchangeCode, adoptTokens, consumePkcePending]
  );

  return <SpotifyCtx.Provider value={value}>{children}</SpotifyCtx.Provider>;
}

export function useSpotifyAuth() {
  const ctx = useContext(SpotifyCtx);
  if (!ctx) throw new Error('useSpotifyAuth must be used inside <SpotifyAuthProvider>');
  return ctx;
}
