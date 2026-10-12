import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

/**
 * Light / dark theme, plus the "Show Bit" preference — the two switches in
 * the account menu.
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
const BIT_KEY = 'vs.showBit';

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

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => (read(THEME_KEY, 'dark') === 'light' ? 'light' : 'dark'));
  const [showBit, setShowBit] = useState(() => read(BIT_KEY, '1') !== '0');

  useEffect(() => { write(THEME_KEY, theme); }, [theme]);
  useEffect(() => { write(BIT_KEY, showBit ? '1' : '0'); }, [showBit]);

  const toggleTheme = useCallback(() => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), []);
  const toggleBit = useCallback(() => setShowBit((v) => !v), []);

  const value = useMemo(
    () => ({ theme, toggleTheme, showBit, toggleBit }),
    [theme, toggleTheme, showBit, toggleBit],
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
