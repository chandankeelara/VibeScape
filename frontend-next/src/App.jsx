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
  const code = params.get('code');

  useEffect(() => {
    if (!code) return;
    // The login feature's Spotify sign-in ALSO returns with ?code=, but that
    // code is redeemed server-side by /api/auth/spotify-oauth. Authorization
    // codes are single-use, so redeeming it here as well would burn it and
    // fail with invalid_grant. Only act on a redirect this provider started.
    if (!consumePkcePending()) return;

    exchangeCode(code).finally(() => {
      params.delete('code');
      params.delete('state');
      setParams(params, { replace: true });
      navigate('/', { replace: true });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

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
