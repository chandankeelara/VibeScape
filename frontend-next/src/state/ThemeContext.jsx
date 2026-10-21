import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

/**
 * Light / dark theme, plus Bit's size (off / medium / large) — the two
 * display preferences in the account menu. Both are per device.
 *
 * The theme attribute is written to <html> by the PLAYER only (useApplyTheme,
 * called from PlayerPage), and removed again when the player unmounts. The
 * landing, login and admin screens were never designed for light, so they
 * stay dark whatever the stored preference is.
 *
 * Dark is the default. The OS preference is NOT followed: the brief is dark by
 * default, and a first visit that flips to light because of an OS setting
 * would contradict every screenshot of the product.
 */

const THEME_KEY = 'vs.theme';
const BIT_KEY = 'vs.bitSize';
/* The on/off switch this setting replaced. Read once, to migrate. */
const LEGACY_BIT_KEY = 'vs.showBit';

export const BIT_SIZES = ['off', 'medium', 'large'];

const ThemeCtx = createContext(null);

function read(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v == null ? fallback : v;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage blocked — the toggle still works, it just won't be remembered */
  }
}

/**
 * The stored Bit size. A device that only ever had the old switch keeps what
 * it chose: '0' was "Show Bit: Off" and stays off; anything else, including
 * never having touched it, was the S2 rig beside the DJ line, which is Medium.
 * Exported for the test.
 */
export function readBitSize() {
  const v = read(BIT_KEY, null);
  if (BIT_SIZES.includes(v)) return v;
  return read(LEGACY_BIT_KEY, '1') === '0' ? 'off' : 'medium';
}

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => (read(THEME_KEY, 'dark') === 'light' ? 'light' : 'dark'));
  const [bitSize, setBitSizeState] = useState(readBitSize);

  useEffect(() => { write(THEME_KEY, theme); }, [theme]);
  useEffect(() => {
    write(BIT_KEY, bitSize);
    // Migrated: the new key is the only one read from here on.
    try { localStorage.removeItem(LEGACY_BIT_KEY); } catch { /* storage blocked */ }
  }, [bitSize]);

  const toggleTheme = useCallback(() => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), []);
  const setBitSize = useCallback((s) => { if (BIT_SIZES.includes(s)) setBitSizeState(s); }, []);

  const value = useMemo(
    () => ({ theme, toggleTheme, bitSize, setBitSize }),
    [theme, toggleTheme, bitSize, setBitSize],
  );
  return <ThemeCtx.Provider value={value}>{children}</ThemeCtx.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeCtx);
  if (!ctx) throw new Error('useTheme must be used inside <ThemeProvider>');
  return ctx;
}

/**
 * Applies the theme to <html> while the calling component is mounted, and
 * re-solves the accent for it (accent text is contrast-solved PER THEME, so a
 * theme flip without a re-solve would leave light-theme text on dark tones).
 */
export function useApplyTheme(vibe, applyAccent) {
  const { theme } = useTheme();
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    const prevMeta = meta?.getAttribute('content');
    meta?.setAttribute('content', theme === 'light' ? '#eceef2' : '#08090b');
    applyAccent(vibe, theme);
    return () => {
      delete root.dataset.theme;
      if (meta && prevMeta) meta.setAttribute('content', prevMeta);
      // Leaving the player (e.g. to /admin) drops back to dark: re-solve so
      // the accent is not left tuned for a light page.
      applyAccent(vibe, 'dark');
    };
    // vibe is applied by PlayerContext on change; here only a theme flip
    // needs a re-solve, so vibe is read but deliberately not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme, applyAccent]);
}
