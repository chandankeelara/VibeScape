/**
 * "Continue with Spotify" for the *signed-out* case.
 *
 * WHY THIS DOES NOT USE useSpotifyAuth() — please read before "fixing" it.
 *
 * SpotifyAuthContext implements the in-app **connect** flow, ported from
 * app.js: an authorize request carrying `code_challenge` (PKCE), redeemed
 * client-side by `exchangeCode()` against accounts.spotify.com. It is the
 * right tool for a user who already has a VibeScape session and wants a
 * streaming token.
 *
 * Login is a different flow and cannot reuse it, for two independent reasons:
 *
 *   1. An OAuth authorization code is SINGLE USE. `POST /api/auth/spotify-oauth`
 *      performs its own exchange server-side (backend/app.py:541 ->
 *      _spotify_token_exchange). Calling `exchangeCode(code)` and then
 *      `api.spotifyOAuthLogin({ code })` redeems the same code twice; the
 *      second call gets invalid_grant. frontend/login.js:141-146 carries a
 *      comment about exactly this bug having bitten before.
 *   2. The backend exchange is a CONFIDENTIAL-client one — it sends
 *      client_id + client_secret and no `code_verifier` (app.py:446-465).
 *      Spotify requires `code_verifier` whenever the authorize request used
 *      `code_challenge`, so a PKCE-initiated code can never be redeemed by
 *      that endpoint.
 *
 * So the login button starts a NON-PKCE authorize exactly as the landing page
 * does (frontend/login.js `startSpotifyLogin`), and the backend does the one
 * and only exchange. After the VibeScape session exists, SpotifyAuthContext
 * takes over for everything else.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';

import * as api from '../../lib/api';

/** Must match SCOPE in state/SpotifyAuthContext.jsx so there's no re-consent. */
const SCOPE =
  'streaming user-read-email user-read-private user-library-read ' +
  'playlist-read-private playlist-read-collaborative user-top-read playlist-modify-private';

/** The /callback bridge page writes the code here (backend/app.py:1636). */
const PENDING_KEY = 'spotify_pending_auth';

const redirectUri = (configured) => configured || `${window.location.origin}/callback`;

/**
 * Pull an authorization code out of wherever the callback left it, and scrub
 * it so a refresh doesn't retry a dead code (legacy `cleanQueryParams`).
 */
function takePendingCode() {
  const params = new URLSearchParams(window.location.search);
  const fromQuery = params.get('spotify_code') || params.get('code');
  const error = params.get('spotify_error') || params.get('error');

  if (fromQuery || error) {
    ['spotify_code', 'spotify_error', 'spotify_state', 'code', 'error', 'state'].forEach((k) =>
      params.delete(k)
    );
    const qs = params.toString();
    window.history.replaceState({}, '', window.location.pathname + (qs ? `?${qs}` : ''));
  }
  if (error) return { code: null, error };
  if (fromQuery) return { code: fromQuery, error: null };

  // Fall back to the localStorage bridge. Always remove it first: leaving a
  // consumed code behind makes the legacy app try to redeem it again.
  try {
    const raw = localStorage.getItem(PENDING_KEY);
    if (!raw) return { code: null, error: null };
    localStorage.removeItem(PENDING_KEY);
    const parsed = JSON.parse(raw);
    // Codes expire in ~60s; anything older is stale rather than ours.
    if (parsed?.ts && Date.now() - parsed.ts > 60_000) return { code: null, error: null };
    return { code: parsed?.code || null, error: parsed?.error || null };
  } catch {
    return { code: null, error: null };
  }
}

export function useSpotifyLogin({ onSuccess, onError }) {
  const [starting, setStarting] = useState(false);
  const handled = useRef(false);

  const config = useQuery({
    queryKey: ['spotify', 'config'],
    queryFn: api.spotifyConfig,
    retry: false,
    staleTime: Infinity,
  });

  const complete = useMutation({
    mutationFn: (code) =>
      api.spotifyOAuthLogin({ code, redirect_uri: redirectUri(config.data?.redirect_uri) }),
    onSuccess,
    onError,
  });

  // Returning from Spotify. Runs once — StrictMode double-invokes effects and
  // a code redeemed twice is a hard failure.
  useEffect(() => {
    if (handled.current || config.isPending) return;
    handled.current = true;
    const { code, error } = takePendingCode();
    if (error) {
      onError?.(new Error(`Spotify sign-in cancelled: ${error}`));
      return;
    }
    if (code) complete.mutate(code);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.isPending]);

  const start = useCallback(() => {
    const clientId = config.data?.client_id;
    if (!clientId) {
      onError?.(new Error('Spotify is not configured on this server.'));
      return;
    }
    setStarting(true);
    // Deliberately no code_challenge — see the file header.
    const params = new URLSearchParams({
      response_type: 'code',
      client_id: clientId,
      redirect_uri: redirectUri(config.data?.redirect_uri),
      scope: SCOPE,
      state: 'vs_next',
      show_dialog: 'false',
    });
    window.location.href = `https://accounts.spotify.com/authorize?${params}`;
  }, [config.data, onError]);

  return {
    start,
    available: Boolean(config.data?.client_id),
    /** True while redirecting out, or while redeeming a code on the way back. */
    pending: starting || complete.isPending,
  };
}
