/**
 * Session state for the React app.
 *
 * Ports the session half of frontend/app.js:641-1040 — `hydrateSessionFromStorage`,
 * `completeLogin`, `signOutOfVibeScape` — with two differences:
 *
 *   1. Hydration is a React Query (`['auth','me']`) rather than a hand-rolled
 *      boolean + try/catch. `lib/api.js` already clears the token on a 401, so
 *      a dead session resolves to "signed out" without a retry loop.
 *   2. The token lives in `lib/session.js` (same localStorage key as the legacy
 *      app) and is read through useSyncExternalStore, so a sign-in or sign-out
 *      anywhere in the tree re-renders every consumer.
 *
 * Teardown of the player, Spotify SDK, queue and recs is NOT done here, by
 * design: this layer only knows about tokens. Sign-out makes AuthGate stop
 * rendering the app, which unmounts PlayerProvider and SpotifyAuthProvider,
 * and their own unmount effects stop playback and disconnect the SDK. Keeping
 * that knowledge in the providers means any other unmount path is covered too.
 */

import { createContext, useCallback, useContext, useMemo, useSyncExternalStore } from 'react';
import { useNavigate } from 'react-router-dom';
import { persistTokensFor } from './SpotifyAuthContext';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import * as api from '../lib/api';
import { getToken, setToken, clearToken, onTokenChange } from '../lib/session';

const AuthCtx = createContext(null);

export const ME_KEY = ['auth', 'me'];

/** Token as reactive state. `onTokenChange` is already a subscribe/unsubscribe pair. */
function useSessionToken() {
  return useSyncExternalStore(onTokenChange, getToken, () => '');
}

export function AuthProvider({ children }) {
  const token = useSessionToken();
  const queryClient = useQueryClient();

  const { data: user, isPending, isError } = useQuery({
    queryKey: ME_KEY,
    queryFn: api.me,
    enabled: Boolean(token),
    retry: false,
    staleTime: Infinity,
  });

  /**
   * Called by the login/signup flows. The auth endpoints return the full user
   * record alongside the token, so seed the cache with it instead of paying for
   * an immediate /api/auth/me round-trip.
   */
  const completeLogin = useCallback(
    (payload) => {
      if (!payload?.session_token) return;
      setToken(payload.session_token);
      // A Spotify sign-in already exchanged a code server-side and handed
      // back a streaming token. Stash it so SpotifyAuthProvider adopts it on
      // mount — one consent screen gets you both identity AND playback.
      persistTokensFor(payload.user_id ?? payload.id, payload);
      queryClient.setQueryData(ME_KEY, payload);
    },
    [queryClient]
  );

  // AuthProvider is mounted inside BrowserRouter (main.jsx -> App -> AuthGate),
  // so this is safe. If AuthGate is ever reused outside a Router, this is the
  // line that will complain.
  const navigate = useNavigate();

  const signOut = useCallback(async () => {
    // Best-effort — a failed logout must still wipe local state, exactly as the
    // legacy `signOutOfVibeScape` did.
    try {
      if (getToken()) await api.logout();
    } catch {
      /* server-side session already gone */
    }
    clearToken();
    queryClient.clear();
    // Back to the main page. Without this you stay on whatever route you
    // signed out from (/admin, say) — the auth card renders over it, and
    // signing back in drops you there instead of the player. `replace` so
    // Back doesn't return to a route that would just show the card again.
    navigate('/', { replace: true });
  }, [queryClient, navigate]);

  const value = useMemo(
    () => ({
      user: token && !isError ? user ?? null : null,
      // No token at all is a settled "signed out", not a loading state.
      isLoading: Boolean(token) && isPending,
      completeLogin,
      signOut,
    }),
    [token, user, isPending, isError, completeLogin, signOut]
  );

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

/** `{ user, isLoading, signOut, completeLogin }`. */
export function useAuth() {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider> (or <AuthGate>)');
  return ctx;
}
