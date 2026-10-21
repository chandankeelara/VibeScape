import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../login';
import { useSpotifyAuth } from '../../state/SpotifyAuthContext';
import { useTheme } from '../../state/ThemeContext';
import { SearchBar } from '../search';
import Icon, { SpotifyMark } from '../../ui/Icon';
import ui from '../../ui/controls.module.css';
import styles from './TopBar.module.css';

const BIT_SIZE_LABELS = [['off', 'Off'], ['medium', 'Medium'], ['large', 'Large']];

const initialsFor = (name) => {
  const s = (name || '').trim();
  if (!s) return '?';
  const parts = s.split(/\s+/);
  return (parts.length === 1 ? parts[0][0] : parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
};

/**
 * Top bar (S2): logo, search in the middle, then help, theme, Spotify state
 * and the account menu. On a phone, search collapses to an icon that opens a
 * full-screen overlay, and help/theme move into the account menu.
 *
 * Everything the old bar offered is still here: library sync (the Spotify
 * chip, and the menu), "Add public playlist", the admin link, sign-out.
 */
export default function TopBar({ onOpenSync, onToggleHelp, searchOpen, onSearchOpen, onSearchClose }) {
  const { user, signOut } = useAuth();
  const { isConnected, isPremium, profile, signIn } = useSpotifyAuth();
  const { theme, toggleTheme, bitSize, setBitSize } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onDown = (e) => { if (!menuRef.current?.contains(e.target)) setMenuOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setMenuOpen(false); };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  const close = (fn) => () => { setMenuOpen(false); fn?.(); };
  const spLabel = isPremium ? 'Spotify Premium' : 'Spotify';
  const spHint = isConnected
    ? (isPremium ? 'Spotify Premium · connected' : `Spotify · ${profile?.display_name || 'connected'} (free)`)
    : 'Spotify not connected';
  const themeIcon = theme === 'dark' ? 'sun' : 'moon';
  const themeLabel = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';

  return (
    <header className={`${styles.topbar} ${searchOpen ? styles.searchOpen : ''}`}>
      <Link to="/" className={styles.logo} aria-label="VibeScape home">
        <span className={styles.logoMark}><Icon name="logo" size={16} /></span>
        <span className={styles.logoText}>VibeScape</span>
      </Link>

      <div className={styles.search}>
        <SearchBar mobileOpen={searchOpen} onMobileClose={onSearchClose} onRequestMobileOpen={onSearchOpen} />
      </div>

      <div className={styles.right}>
        <button type="button" className={`${ui.ib} ${styles.phoneOnly}`} onClick={onSearchOpen} aria-label="Search">
          <Icon name="search" />
        </button>
        <button type="button" className={`${ui.ib} ${styles.deskOnly}`} onClick={onToggleHelp} aria-label="Keyboard shortcuts" title="Keyboard shortcuts (?)">
          <Icon name="help" />
        </button>
        <button type="button" className={`${ui.ib} ${styles.deskOnly}`} onClick={toggleTheme} aria-label={themeLabel} title={themeLabel}>
          <Icon name={themeIcon} />
        </button>

        {isConnected ? (
          <button
            type="button"
            className={`${ui.pill} ${styles.spChip}`}
            onClick={onOpenSync}
            aria-label={`${spLabel} connected. Sync your Spotify library`}
            title="Sync your Spotify library"
          >
            <span className={styles.spDot} aria-hidden="true" />
            <span className={styles.spLogo}><SpotifyMark size={22} color="var(--spotify)" wave="var(--bg)" /></span>
            <span className={styles.spLabel}>{spLabel}</span>
          </button>
        ) : (
          <button type="button" className={`${ui.pill} ${styles.spSignin}`} onClick={signIn} aria-label="Sign in with Spotify">
            <SpotifyMark size={16} color="currentColor" wave="var(--sp-wave)" />
            <span className={styles.spLabel}>Sign in with Spotify</span>
          </button>
        )}

        {user && (
          <div className={styles.account} ref={menuRef}>
            <button
              type="button"
              className={styles.avatarBtn}
              aria-label="Account menu"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
            >
              <span className={styles.avatar} aria-hidden="true">{initialsFor(user.display_name)}</span>
            </button>

            {menuOpen && (
              <div className={styles.menu} role="menu">
                <div className={styles.mhead}>
                  <b>{user.display_name || 'You'}</b>
                  <span>{spHint}</span>
                </div>
                {isConnected ? (
                  <button type="button" role="menuitem" className={styles.mi} onClick={close(onOpenSync)}>
                    <span>Sync Spotify library</span><Icon name="sync" size={18} />
                  </button>
                ) : (
                  <button type="button" role="menuitem" className={styles.mi} onClick={close(signIn)}>
                    <span>Connect Spotify</span><SpotifyMark size={18} color="currentColor" wave="var(--raised)" />
                  </button>
                )}
                <button type="button" role="menuitem" className={styles.mi} onClick={close(onOpenSync)}>
                  <span>Add public playlist</span><Icon name="link" size={18} />
                </button>
                {/* Three sizes, so a segmented row rather than an item that
                    cycles: a cycling item hides two of the three choices. The
                    menu stays open, so the change can be seen as it lands. */}
                <div className={styles.mrow}>
                  <span id="bit-size-label">Bit</span>
                  <div className={`${ui.seg} ${styles.mseg}`} role="group" aria-labelledby="bit-size-label">
                    {BIT_SIZE_LABELS.map(([size, label]) => (
                      <button
                        key={size}
                        type="button"
                        role="menuitemradio"
                        aria-checked={bitSize === size}
                        className={`${ui.segBtn} ${styles.msegBtn}`}
                        onClick={() => setBitSize(size)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
                <button type="button" role="menuitem" className={styles.mi} onClick={toggleTheme}>
                  <span>Theme</span><span className={styles.mval}>{theme === 'dark' ? 'Dark' : 'Light'}</span>
                </button>
                <button type="button" role="menuitem" className={styles.mi} onClick={close(onToggleHelp)}>
                  <span>Keyboard shortcuts</span><Icon name="help" size={18} />
                </button>
                {user.is_admin && (
                  <Link className={styles.mi} to="/admin" role="menuitem" onClick={() => setMenuOpen(false)}>
                    <span>Admin panel</span><Icon name="shield" size={18} />
                  </Link>
                )}
                <div className={styles.msep} />
                <button type="button" role="menuitem" className={styles.mi} onClick={signOut}>
                  <span>Sign out</span><Icon name="out" size={18} />
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
