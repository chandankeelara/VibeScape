import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../login';
import { useSpotifyAuth } from '../../state/SpotifyAuthContext';
import styles from './TopBar.module.css';

const initialsFor = (name) => {
  const s = (name || '').trim();
  if (!s) return '?';
  const parts = s.split(/\s+/);
  return (parts.length === 1 ? parts[0][0] : parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
};

/**
 * Top bar: wordmark, user menu, Spotify connect/sync.
 * Ported from frontend/index.html:107-158 and app.js:650-1040 (updateUserMenu).
 */
export default function TopBar({ onOpenSync, onToggleHelp }) {
  const { user, signOut } = useAuth();
  const { isConnected, isPremium, profile, signIn } = useSpotifyAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e) => { if (!menuRef.current?.contains(e.target)) setMenuOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setMenuOpen(false); };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  return (
    <header className={styles.topbar}>
      <div className={styles.wordmark}>
        <span className={styles.dot} aria-hidden="true" />
        <span className={styles.text}>Vibe<em>Scape</em></span>
      </div>

      <div className={styles.right}>
        <button className={styles.help} type="button" onClick={onToggleHelp} aria-label="Keyboard shortcuts" title="Keyboard shortcuts (?)">?</button>

        {isConnected ? (
          <button className={styles.sync} type="button" onClick={onOpenSync} aria-label="Sync my Spotify library" title="Sync my Spotify library">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" />
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10" /><path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14" />
            </svg>
          </button>
        ) : (
          <button className={styles.spotify} type="button" onClick={signIn}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.59 14.42a.62.62 0 0 1-.86.21c-2.36-1.44-5.33-1.77-8.83-.97a.62.62 0 1 1-.28-1.22c3.83-.87 7.13-.49 9.77 1.12.3.18.39.57.2.86zm1.23-2.74a.78.78 0 0 1-1.07.26c-2.7-1.66-6.82-2.14-10.02-1.17a.78.78 0 1 1-.45-1.5c3.66-1.1 8.2-.57 11.29 1.33.37.23.49.71.25 1.08zm.11-2.85c-3.24-1.92-8.59-2.1-11.68-1.16a.94.94 0 1 1-.54-1.8c3.55-1.07 9.45-.86 13.19 1.36a.94.94 0 0 1-.97 1.6z" />
            </svg>
            <span>Sign in with Spotify</span>
          </button>
        )}

        {user && (
          <div className={styles.userMenu} ref={menuRef}>
            <button
              className={styles.userPill}
              type="button"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
            >
              <span className={styles.avatar} aria-hidden="true">{initialsFor(user.display_name)}</span>
              <span className={styles.userName}>{user.display_name || 'You'}</span>
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </button>

            {menuOpen && (
              <div className={styles.popover} role="menu">
                <div className={styles.popHeader}>
                  <div className={styles.popName}>{user.display_name || 'You'}</div>
                  <div className={styles.popHint}>
                    {isConnected ? (isPremium ? 'Spotify Premium' : `Spotify: ${profile?.display_name || 'connected'}`) : 'signed in on this device'}
                  </div>
                </div>
                <button className={styles.popItem} type="button" role="menuitem" onClick={() => { setMenuOpen(false); onOpenSync(); }}>
                  Add public playlist
                </button>
                {user.is_admin && (
                  <Link className={styles.popItem} to="/admin" role="menuitem" onClick={() => setMenuOpen(false)}>
                    Admin panel
                  </Link>
                )}
                <button className={`${styles.popItem} ${styles.popDanger}`} type="button" role="menuitem" onClick={signOut}>
                  Sign out of VibeScape
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
