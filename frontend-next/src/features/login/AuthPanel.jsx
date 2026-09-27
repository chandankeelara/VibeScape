/**
 * The auth card — Spotify / guest / email sign-in, and account creation.
 *
 * Card chrome and view layout come from frontend/index.html:37-105 and
 * frontend/style.css:2985-3310. The *flows* come from frontend/login.js,
 * which is the only legacy auth code that matches the current backend.
 */

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';

import * as api from '../../lib/api';
import { useToast } from '../../state/ToastContext';
import styles from './Auth.module.css';
import { OptionButton, MailIcon, PlayIcon, PlusIcon, SpotifyIcon } from './parts';
import { useSpotifyLogin } from './useSpotifyLogin';
import EmailForm from './EmailForm';

const TITLES = {
  choose: "Who's listening?",
  signin: 'Welcome back',
  create: 'New profile',
};

export default function AuthPanel({ onAuthenticated }) {
  const toast = useToast();
  const [view, setView] = useState('choose');

  const spotify = useSpotifyLogin({
    onSuccess: (payload) => {
      toast(`Welcome back, ${payload?.display_name || 'listener'}.`, 'success');
      onAuthenticated(payload);
    },
    onError: (err) => toast(err?.message || 'Spotify sign-in failed.', 'error'),
  });

  const guest = useMutation({
    mutationFn: api.guestLogin,
    onSuccess: (payload) => {
      toast('Demo session started — nothing is saved.', 'info');
      onAuthenticated(payload);
    },
    onError: () => toast('Could not start the demo session.', 'error'),
  });

  const busy = spotify.pending || guest.isPending;

  return (
    <div className={styles.overlay} role="dialog" aria-modal="true" aria-labelledby="authCardTitle">
      <div className={styles.backdrop} />
      <div className={styles.card}>
        <header className={styles.header}>
          <div className={styles.brand}>
            <span className={styles.brandDot} aria-hidden="true" />
            <span className={styles.brandName}>VibeScape</span>
          </div>
          <h1 className={styles.title} id="authCardTitle">
            {TITLES[view]}
          </h1>
        </header>

        {view === 'choose' && (
          <div className={styles.view}>
            <div className={styles.options}>
              <OptionButton
                variant="spotify"
                icon={<SpotifyIcon />}
                title={spotify.pending ? 'Connecting to Spotify…' : 'Continue with Spotify'}
                sub="No password — brings your library with you"
                onClick={spotify.start}
                disabled={busy || !spotify.available}
              />
              <OptionButton
                icon={<MailIcon />}
                title="Sign in with email"
                sub="For profiles created with a password"
                onClick={() => setView('signin')}
                disabled={busy}
              />
              <OptionButton
                icon={<PlayIcon />}
                title={guest.isPending ? 'Starting demo…' : 'Just listen'}
                sub="No signup — curated library, guest sessions are ephemeral"
                onClick={() => guest.mutate()}
                disabled={busy}
              />
            </div>

            {!spotify.available && (
              <p className={styles.hint}>
                Spotify sign-in is unavailable — the server has no client ID configured.
              </p>
            )}

            <div className={styles.footer}>
              <button
                className={`${styles.btn} ${styles.btnSecondary}`}
                type="button"
                onClick={() => setView('create')}
                disabled={busy}
              >
                <PlusIcon />
                <span>Create new profile</span>
              </button>
            </div>
          </div>
        )}

        {(view === 'signin' || view === 'create') && (
          <EmailForm mode={view} onBack={() => setView('choose')} onSuccess={onAuthenticated} />
        )}
      </div>
    </div>
  );
}
