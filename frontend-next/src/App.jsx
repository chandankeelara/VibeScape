import { useEffect } from 'react';
import { Routes, Route, Navigate, useSearchParams, useNavigate } from 'react-router-dom';

import AuthGate, { useAuth } from './features/login';
import { PlayerProvider } from './state/PlayerContext';
import { SpotifyAuthProvider, useSpotifyAuth } from './state/SpotifyAuthContext';
import PlayerPage from './features/player/PlayerPage';
import AdminPage from './features/admin/AdminPage';

/**
 * Handles the ?code= return leg of the Spotify PKCE redirect, then strips the
 * query so a refresh doesn't attempt a second exchange with a spent code.
 */
function SpotifyCallback() {
  const [params, setParams] = useSearchParams();
  const { exchangeCode, consumePkcePending } = useSpotifyAuth();
  const navigate = useNavigate();

  /*
   * The code arrives as `spotify_code`, NOT `code`.
   *
   * Every authorize request in this app — login and in-app connect alike —
   * uses SPOTIFY_REDIRECT_URI, which points at the backend's /callback
   * bridge page (backend/app.py:1700). That page does not hand the query
   * through untouched: it RENAMES the parameters on the way back, so
   * `?code=` becomes `?spotify_code=` (app.py:1672).
   *
   * This handler only ever looked for `code`, so it never fired. The
   * TopBar's "Sign in with Spotify" redirected, came back through the
   * bridge, and silently dropped the code — the button simply stayed
   * unconnected. The login path survived because useSpotifyLogin reads both
   * names (useSpotifyLogin.js:53), but that hook is mounted only while
   * signed OUT, so it could not rescue an in-app connect.
   *
   * Both names are accepted here for the same reason it reads both there:
   * a direct redirect_uri that skips the bridge still yields plain `code`.
   */
  const code = params.get('spotify_code') || params.get('code');
  const error = params.get('spotify_error') || params.get('error');

  useEffect(() => {
    if (!code && !error) return;
    // The login feature's Spotify sign-in ALSO returns this way, but that
    // code is redeemed server-side by /api/auth/spotify-oauth. Authorization
    // codes are single-use, so redeeming it here as well would burn it and
    // fail with invalid_grant. Only act on a redirect this provider started.
    if (!consumePkcePending()) return;

    const scrub = () => {
      ['spotify_code', 'spotify_error', 'spotify_state', 'code', 'error', 'state']
        .forEach((k) => params.delete(k));
      setParams(params, { replace: true });
      navigate('/', { replace: true });
    };

    if (error) {
      scrub();
      return;
    }
    exchangeCode(code).finally(scrub);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, error]);

  return null;
}

/**
 * Providers that need an authenticated user sit inside AuthGate, so the
 * Spotify token namespace (spotify_{userId}_*) is correct from first render —
 * two profiles on one device must not share a Spotify session.
 */
function AuthedApp() {
  const { user } = useAuth();

  return (
    <SpotifyAuthProvider userId={user?.user_id ?? user?.id ?? '_anon'}>
      <PlayerProvider>
        <SpotifyCallback />
        <Routes>
          <Route path="/" element={<PlayerPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </PlayerProvider>
    </SpotifyAuthProvider>
  );
}

export default function App() {
  return (
    <AuthGate>
      <AuthedApp />
    </AuthGate>
  );
}
