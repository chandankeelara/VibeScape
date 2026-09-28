/**
 * #authModal — frontend/login.html:504-588, frontend/login.js:222-323.
 *
 * Provider list on top (Spotify live; three placeholders with a "Soon" badge,
 * exactly as legacy), then the email/create tabs.
 *
 * The email form is NOT reimplemented here: it is the login feature's
 * `EmailForm`, which already owns the /api/auth/login + /api/auth/signup calls
 * and the backend error-code → message mapping. Duplicating that logic to get
 * landing-page chrome would mean two copies of the mapping drifting apart.
 * Spotify is likewise `useSpotifyLogin` (owned by the caller so its
 * return-from-OAuth effect runs whether or not this modal is open).
 */

import { useEffect, useId, useRef } from 'react';

import EmailForm from '../login/EmailForm';
import { SerifEm } from './bits';
import {
  AmazonMusicIcon,
  AppleMusicIcon,
  CloseIcon,
  SpotifyIcon,
  YouTubeMusicIcon,
} from './icons';
import styles from './AuthModal.module.css';

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

const SOON = [
  { key: 'ytmusic', label: 'YouTube Music', Icon: YouTubeMusicIcon },
  { key: 'apple', label: 'Apple Music', Icon: AppleMusicIcon },
  { key: 'amazon', label: 'Amazon Music', Icon: AmazonMusicIcon },
];

export default function AuthModal({ tab, onTabChange, onClose, spotify, onAuthenticated }) {
  const cardRef = useRef(null);
  const titleId = useId();
  const returnFocusTo = useRef(null);

  // Trap focus inside the dialog and return it to the opener on close —
  // legacy had neither, and a modal you can Tab out of is a real a11y bug.
  useEffect(() => {
    returnFocusTo.current = document.activeElement;
    const card = cardRef.current;
    // EmailForm focuses its own email field on mount; don't fight it.
    if (card && !card.contains(document.activeElement)) {
      card.querySelector(FOCUSABLE)?.focus();
    }

    function onKeyDown(ev) {
      if (ev.key === 'Escape') {
        ev.stopPropagation();
        onClose();
        return;
      }
      if (ev.key !== 'Tab' || !card) return;
      const items = Array.from(card.querySelectorAll(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null
      );
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (ev.shiftKey && document.activeElement === first) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && document.activeElement === last) {
        ev.preventDefault();
        first.focus();
      }
    }

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      returnFocusTo.current?.focus?.();
    };
  }, [onClose]);

  return (
    <div className={styles.overlay} role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <button className={styles.backdrop} type="button" aria-label="Close sign-in" onClick={onClose} />

      <div className={styles.card} ref={cardRef}>
        <button className={styles.close} type="button" onClick={onClose} aria-label="Close">
          <CloseIcon />
        </button>

        <div className={styles.head}>
          <h2 className={styles.title} id={titleId}>
            <span>Log in to</span> <SerifEm className={styles.titleSerif}>VibeScape</SerifEm>
          </h2>
          <p className={styles.sub}>Pick a provider — or use email.</p>
        </div>

        <div className={styles.providers}>
          <button
            className={`${styles.provider} ${styles.spotify}`}
            type="button"
            onClick={spotify.start}
            disabled={spotify.pending || !spotify.available}
          >
            <span className={styles.providerIcon} aria-hidden="true">
              <SpotifyIcon />
            </span>
            <span className={styles.providerLabel}>
              {spotify.pending ? 'Connecting to ' : 'Continue with '}
              <strong>Spotify</strong>
            </span>
            <span className={styles.providerArrow} aria-hidden="true">
              →
            </span>
          </button>

          {SOON.map(({ key, label, Icon }) => (
            <button
              key={key}
              className={`${styles.provider} ${styles[key]}`}
              type="button"
              disabled
              aria-disabled="true"
            >
              <span className={styles.providerIcon} aria-hidden="true">
                <Icon />
              </span>
              <span className={styles.providerLabel}>
                Continue with <strong>{label}</strong>
              </span>
              <span className={styles.badge}>Soon</span>
            </button>
          ))}
        </div>

        {!spotify.available && (
          <p className={styles.unavailable}>
            Spotify sign-in is unavailable — the server has no client ID configured.
          </p>
        )}

        <div className={styles.divider} aria-hidden="true">
          <span>or</span>
        </div>

        <div className={styles.tabs} role="tablist" aria-label="Email sign-in mode">
          {[
            ['signin', 'Log in'],
            ['create', 'Create account'],
          ].map(([value, label]) => (
            <button
              key={value}
              className={`${styles.tab} ${tab === value ? styles.tabActive : ''}`}
              type="button"
              role="tab"
              aria-selected={tab === value}
              onClick={() => onTabChange(value)}
            >
              {label}
            </button>
          ))}
        </div>

        {/* `key` remounts the form on tab change so its local error state and
            focus reset, which is what the legacy tab switch effectively did. */}
        <div className={styles.formSlot}>
          <EmailForm key={tab} mode={tab} onBack={onClose} onSuccess={onAuthenticated} />
        </div>
      </div>
    </div>
  );
}
