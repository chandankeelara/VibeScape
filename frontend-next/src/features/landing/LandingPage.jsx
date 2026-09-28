/**
 * The signed-out entry point: VibeScape's marketing landing page.
 *
 * Ported from frontend/login.html + login.css + login.js. Structure and copy
 * are the legacy page's; the auth *flows* are not reimplemented — they are the
 * existing React ones:
 *
 *   - Spotify  → features/login/useSpotifyLogin (non-PKCE, server-side
 *                exchange; read that file's header before touching it)
 *   - email    → features/login/EmailForm, rendered inside the modal
 *   - guest    → api.guestLogin()
 *
 * All three hand their payload to `completeLogin` from AuthContext, which
 * seeds the session; AuthGate then swaps this page out for the player. There
 * is no navigation here and no localStorage poking — legacy did both by hand.
 */

import { useCallback, useState } from 'react';
import { useMutation } from '@tanstack/react-query';

import * as api from '../../lib/api';
import { useAuth } from '../../state/AuthContext';
import { useSpotifyLogin } from '../login/useSpotifyLogin';
import AuthModal from './AuthModal';
import Faq from './Faq';
import Features from './Features';
import Hero from './Hero';
import MobileSplit from './MobileSplit';
import SiteFoot from './SiteFoot';
import SiteNav from './SiteNav';
import StatusLine, { useStatus } from './StatusLine';
import WorksWith from './WorksWith';
import { useVibeDemo } from './useVibeDemo';
import styles from './LandingPage.module.css';

export default function LandingPage({ onAuthenticated }) {
  const { completeLogin } = useAuth();
  const [status, setStatus] = useStatus();
  const [modalTab, setModalTab] = useState(null); // null | 'signin' | 'create'
  const demo = useVibeDemo();

  const authenticated = useCallback(
    (payload) => {
      (onAuthenticated ?? completeLogin)(payload);
    },
    [onAuthenticated, completeLogin]
  );

  const spotify = useSpotifyLogin({
    onSuccess: (payload) => {
      setStatus(`Welcome back${payload?.display_name ? `, ${payload.display_name}` : ''} — loading your library…`);
      authenticated(payload);
    },
    onError: (err) => setStatus(err?.message || 'Spotify sign-in failed.', 'error'),
  });

  const guest = useMutation({
    mutationFn: api.guestLogin,
    onMutate: () => setStatus('Starting demo session…'),
    onSuccess: (payload) => authenticated(payload),
    onError: (err) => setStatus(`Could not start demo: ${err?.message || 'unknown error'}`, 'error'),
  });

  const closeModal = useCallback(() => setModalTab(null), []);

  return (
    <div className={styles.page} id="top" style={{ '--accent': demo.accent }}>
      <a className={styles.skip} href="#features">
        Skip to features
      </a>

      <SiteNav />

      <main className={styles.main}>
        <Hero
          demo={demo}
          onLogin={() => setModalTab('signin')}
          onGuest={() => guest.mutate()}
          guestPending={guest.isPending}
        />
        <MobileSplit />
        <Features />
        <WorksWith />
        <Faq />
      </main>

      <SiteFoot />

      <StatusLine status={status} />

      {modalTab && (
        <AuthModal
          tab={modalTab}
          onTabChange={setModalTab}
          onClose={closeModal}
          spotify={spotify}
          onAuthenticated={authenticated}
        />
      )}
    </div>
  );
}
