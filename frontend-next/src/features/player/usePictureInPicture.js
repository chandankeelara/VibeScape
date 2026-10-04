import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Document Picture-in-Picture — the Spotify-style miniplayer window.
 *
 * This is the DOCUMENT PiP API (`window.documentPictureInPicture`), not the
 * `<video>` one. It hands back a real, empty, always-on-top browser window
 * whose document we populate ourselves, which is the only way to get OUR
 * controls and OUR metadata into it rather than a bare video surface.
 *
 * CHROME / EDGE ONLY. Firefox and Safari ship neither. `supported` is a plain
 * feature detection and the control is not RENDERED where it is false — a
 * button that silently does nothing is worse than no button.
 *
 * WHAT THIS HOOK DOES NOT DO: it does not move any existing DOM into the new
 * window. Moving a node across documents REPARENTS it, and reparenting an
 * <iframe> reloads it — the YouTube player would restart from zero. The
 * miniplayer is therefore a second VIEW, rendered by React through a portal
 * into this window's body, over the same PlayerContext state. See
 * MiniPlayer.jsx.
 *
 * STYLES DO NOT INHERIT INTO A PiP WINDOW. It is a separate document with its
 * own (empty) stylesheet set, so copyStyles() below ports ours across. The
 * hashed CSS-Module class names keep working the moment the sheets are there —
 * they are just class names.
 */

/* Evaluated once at module scope: the capability cannot appear mid-session,
   and this keeps it out of every render. */
export const PIP_SUPPORTED =
  typeof window !== 'undefined' && 'documentPictureInPicture' in window;

/**
 * Requested INNER size. Chrome clamps to its own minimum and remembers what
 * the user resized to afterwards, so this is a first-run hint, not a contract
 * — MiniPlayer.module.css is written to survive being resized either way.
 */
const SIZE = { width: 400, height: 184 };

/**
 * Port every stylesheet in the main document into the PiP document.
 *
 * Same-origin sheets (ours, including Vite's injected <style> blocks in dev
 * and the emitted bundle in prod) are serialised rule by rule. A cross-origin
 * sheet — the Google Fonts link in index.html — throws on `.cssRules`, so it
 * is re-linked by href instead and the PiP window fetches it itself.
 *
 * Deliberately a ONE-SHOT copy at open time. A MutationObserver that kept the
 * two in sync would exist to catch Vite's dev-mode HMR and the lazily imported
 * panel chunks, neither of which renders anything inside this window.
 */
function copyStyles(pip) {
  const doc = pip.document;

  for (const sheet of Array.from(document.styleSheets)) {
    try {
      const text = Array.from(sheet.cssRules).map((r) => r.cssText).join('\n');
      const style = doc.createElement('style');
      style.textContent = text;
      if (sheet.media?.mediaText) style.media = sheet.media.mediaText;
      doc.head.appendChild(style);
    } catch {
      // Cross-origin: unreadable rules, but the href is still usable.
      if (!sheet.href) continue;
      const link = doc.createElement('link');
      link.rel = 'stylesheet';
      link.href = sheet.href;
      if (sheet.media?.mediaText) link.media = sheet.media.mediaText;
      doc.head.appendChild(link);
    }
  }

  /*
   * Constructed stylesheets cannot be adopted across documents — they are
   * bound to the realm that made them — so they are REBUILT with the PiP
   * window's own CSSStyleSheet constructor. Nothing in the app uses these
   * today; this is here so a future CSS-in-JS runtime does not silently ship
   * an unstyled miniplayer.
   */
  const adopted = document.adoptedStyleSheets || [];
  if (adopted.length && typeof pip.CSSStyleSheet === 'function') {
    const copies = [];
    for (const sheet of adopted) {
      try {
        const s = new pip.CSSStyleSheet();
        s.replaceSync(Array.from(sheet.cssRules).map((r) => r.cssText).join('\n'));
        copies.push(s);
      } catch { /* unreadable — skip rather than abort the whole copy */ }
    }
    try { doc.adoptedStyleSheets = copies; } catch { /* not supported */ }
  }
}

export default function usePictureInPicture() {
  const [pipWindow, setPipWindow] = useState(null);
  // State is what renders the portal; the ref is what cleanup and the
  // re-entrancy guard read, because neither can wait for a re-render.
  const winRef = useRef(null);

  const forget = useCallback(() => {
    winRef.current = null;
    setPipWindow(null);
  }, []);

  const close = useCallback(() => {
    const win = winRef.current;
    forget();
    // Fires 'pagehide', which re-enters forget() — idempotent by construction,
    // the same shape listenLog uses for its single open play.
    try { win?.close(); } catch { /* already gone */ }
  }, [forget]);

  const open = useCallback(async () => {
    if (!PIP_SUPPORTED || winRef.current) return;

    /*
     * Exactly one PiP window may exist per tab. A stale one can survive an
     * HMR update or a remount of this hook, and requestWindow() would then
     * reject; closing it first makes the button always work.
     */
    try { window.documentPictureInPicture.window?.close(); } catch { /* none */ }

    let win;
    try {
      // Must be called from a user gesture. The toggle's click is one.
      win = await window.documentPictureInPicture.requestWindow({
        width: SIZE.width,
        height: SIZE.height,
      });
    } catch (e) {
      console.warn('[VibeScape] picture-in-picture refused:', e);
      return;
    }

    win.document.title = 'VibeScape';
    win.document.documentElement.lang = 'en';
    copyStyles(win);

    /*
     * The ONLY close signal we get. It covers the user clicking the window's
     * own close button, the browser reclaiming the window, and our own
     * close() — so there is one path back to "no portal", and no listener
     * left behind on a window that no longer exists.
     */
    win.addEventListener('pagehide', forget, { once: true });

    winRef.current = win;
    setPipWindow(win);
  }, [forget]);

  const toggle = useCallback(() => {
    if (winRef.current) close();
    else open();
  }, [close, open]);

  // Tearing the player down (sign-out, route change) must take the window
  // with it, or an orphaned always-on-top panel outlives the session.
  useEffect(() => () => {
    const win = winRef.current;
    winRef.current = null;
    try { win?.close(); } catch { /* already gone */ }
  }, []);

  return { supported: PIP_SUPPORTED, pipWindow, open, close, toggle };
}
