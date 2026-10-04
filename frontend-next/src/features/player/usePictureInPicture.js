import { useCallback, useEffect, useRef, useState } from 'react';
import * as player from '../../media/player';
import { useToast } from '../../state/ToastContext';

/**
 * Document Picture-in-Picture — the Spotify-style miniplayer window, opened
 * either by the button or BY ITSELF when the user switches away from the tab.
 *
 * This is the DOCUMENT PiP API (`window.documentPictureInPicture`), not the
 * `<video>` one. It hands back a real, empty, always-on-top browser window
 * whose document we populate ourselves, which is the only way to get OUR
 * controls and OUR metadata into it rather than a bare video surface.
 *
 * CHROME / EDGE ONLY. Firefox and Safari ship neither this nor the media
 * session action below. `supported` is a plain feature detection and the
 * control is not RENDERED where it is false — a button that silently does
 * nothing is worse than no button.
 *
 * WHAT THIS HOOK DOES NOT DO: it does not move any existing DOM into the new
 * window. Moving a node across documents REPARENTS it, and reparenting an
 * <iframe> reloads it — the YouTube player would restart from zero. The
 * miniplayer is a second VIEW, rendered by React through a portal into this
 * window's body, over the same PlayerContext state. See MiniPlayer.jsx.
 *
 * ---------------------------------------------------------------------------
 * AUTOMATIC OPENING, AND THE PERMISSION THAT GATES IT
 *
 * `requestWindow()` requires transient user activation, so a `visibilitychange`
 * handler cannot call it. The only sanctioned path is Chrome's AUTOMATIC
 * PICTURE-IN-PICTURE: register a media-session action handler for
 * `'enterpictureinpicture'` and Chrome invokes it, with activation, when the
 * tab is occluded. Document PiP is explicitly supported from that handler.
 *
 * VERIFIED about the gating (the published eligibility list for the media
 * -playback path, Chrome 134+):
 *   - top frame URL safe per Safe Browsing
 *   - the media lives in the TOP FRAME
 *   - audible within the last two seconds, holds audio focus, is PLAYING
 *   - a handler for 'enterpictureinpicture' is registered
 *   - the user's Media Engagement Index threshold is exceeded, unless the
 *     user has explicitly allowed the feature
 * There is NO installed-PWA requirement on this path. (The older Chrome 120
 * path is for apps capturing camera/mic via getUserMedia and does not apply.)
 *
 * ALSO VERIFIED, and it decides the design: the "Automatic picture-in-picture"
 * capability is a Chrome CONTENT SETTING, not a Permissions-API permission.
 * It is absent from Chromium's permission_descriptor.idl, so
 * `navigator.permissions.query()` cannot READ it and there is no
 * `requestPermission()` to call. CHROME asks, contextually, the first time the
 * conditions above are met; we cannot trigger that prompt and cannot observe
 * its answer. Any "check the permission first" branch would be fiction.
 *
 * So consent is a CHAIN, and the user is told so:
 *   1. We ask, in our own UI, once — at the first moment the question makes
 *      sense (they switched away while something was playing). Until they say
 *      yes the handler is NOT registered, so the page is not even eligible and
 *      Chrome never prompts.
 *   2. Chrome then asks its own question the first time it would fire.
 * Declining either one leaves the explicit button working exactly as before.
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

/* Tri-state, because "never asked" and "said no" are different answers and
   only one of them may be asked again. Absent key = never asked. */
const AUTO_KEY = 'vs.player.pipAuto';
const UNASKED = 'unasked';
const ON = 'on';
const OFF = 'off';

/**
 * How long the opener must STAY hidden after an auto-opened window closes
 * before we read that close as a dismissal.
 *
 * Chrome closes the auto-opened window itself when the page becomes visible
 * again, and we cannot rely on the ordering of that `pagehide` against the
 * opener's `visibilitychange`. Without this delay a perfectly normal return to
 * the tab could be misread as "the user shut it down", and the feature would
 * switch itself off the first time it worked.
 */
const DISMISS_CONFIRM_MS = 1200;

function loadAuto() {
  try {
    const v = localStorage.getItem(AUTO_KEY);
    return v === '1' ? ON : v === '0' ? OFF : UNASKED;
  } catch {
    return UNASKED;
  }
}

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

/**
 * A refusal the user can act on.
 *
 * `requestWindow()` rejects for reasons that are all environmental — no
 * activation left, no window to parent to (an embedded browser pane does
 * this, with `InvalidStateError: Internal error: no window`), policy. None of
 * them is worth naming at the user, but silence is worse: the first pass only
 * logged a console warning, so a click did visibly nothing.
 */
function refusalMessage(err) {
  if (err?.name === 'NotAllowedError') {
    return 'The browser blocked the miniplayer window. Try clicking the button again.';
  }
  return 'This browser window cannot open a miniplayer. It needs a normal Chrome or Edge window.';
}

export default function usePictureInPicture() {
  const toast = useToast();

  const [pipWindow, setPipWindow] = useState(null);
  const [autoPref, setAutoPref] = useState(loadAuto);

  const winRef = useRef(null);
  /* Did THIS window open by itself? Decides whether returning to the tab
     closes it, and whether closing it counts as a dismissal. */
  const autoOpenedRef = useRef(false);
  const autoRef = useRef(autoPref);
  const dismissTimerRef = useRef(0);
  /* Work that can only happen with the tab in front of the user: a toast
     nobody would see otherwise, and the one-time consent question. */
  const noticeRef = useRef(null);
  const pendingAskRef = useRef(false);

  // Same shape useTheaterMode uses for `theater`: a mirror written during
  // render so the imperative paths below read the live value without
  // re-subscribing. Writing the same value twice under StrictMode is a no-op.
  autoRef.current = autoPref;

  // Persisted in an effect rather than inside the state updater: StrictMode
  // double-invokes updaters, and storage is a side effect.
  useEffect(() => {
    try {
      if (autoPref === UNASKED) localStorage.removeItem(AUTO_KEY);
      else localStorage.setItem(AUTO_KEY, autoPref === ON ? '1' : '0');
    } catch { /* storage off — the session still works, it just won't stick */ }
  }, [autoPref]);

  /* ------------------------------------------------------------- lifecycle */

  const forget = useCallback(() => {
    const wasAuto = autoOpenedRef.current;
    autoOpenedRef.current = false;
    winRef.current = null;
    setPipWindow(null);

    /*
     * THE OFF SWITCH.
     *
     * Closing an AUTO-opened window from the window's own close button, while
     * the tab is still hidden, is the one unambiguous "stop doing this" — the
     * user is looking straight at the thing they are rejecting. Returning to
     * the tab is NOT a dismissal; that is the designed lifecycle, so it is
     * excluded by the visibility check and by the delay (see
     * DISMISS_CONFIRM_MS).
     *
     * The way back is the button, which re-arms and says so.
     */
    if (!wasAuto || document.visibilityState !== 'hidden') return;
    clearTimeout(dismissTimerRef.current);
    dismissTimerRef.current = setTimeout(() => {
      if (document.visibilityState !== 'hidden') return;
      setAutoPref(OFF);
      noticeRef.current = {
        msg: 'Miniplayer won’t open on its own any more. The miniplayer button turns it back on.',
        kind: 'info',
      };
    }, DISMISS_CONFIRM_MS);
  }, []);

  const close = useCallback(() => {
    const win = winRef.current;
    forget();
    // Fires 'pagehide', which re-enters forget() — idempotent by construction,
    // the same shape listenLog uses for its single open play.
    try { win?.close(); } catch { /* already gone */ }
  }, [forget]);

  const open = useCallback(async ({ auto = false } = {}) => {
    if (!PIP_SUPPORTED || winRef.current) return;

    /*
     * Exactly one PiP window may exist per tab. A stale one can survive an
     * HMR update or a remount of this hook, and requestWindow() would then
     * reject; closing it first makes the button always work.
     */
    try { window.documentPictureInPicture.window?.close(); } catch { /* none */ }

    let win;
    try {
      /*
       * Must be called with transient activation. The button's click is one;
       * on the automatic path Chrome grants activation to the media-session
       * callback, which is the entire reason that path exists. Nothing is
       * awaited before this call, so the activation is still live.
       */
      win = await window.documentPictureInPicture.requestWindow({
        width: SIZE.width,
        height: SIZE.height,
      });
    } catch (e) {
      console.warn('[VibeScape] picture-in-picture refused:', e);
      // Only for a deliberate click. Nobody is looking at the tab on the
      // automatic path, and Chrome declining to auto-open is routine.
      if (!auto) toast(refusalMessage(e), 'warning');
      return;
    }

    win.document.title = 'VibeScape';
    win.document.documentElement.lang = 'en';
    copyStyles(win);

    /*
     * The ONLY close signal we get. It covers the user closing the window,
     * Chrome closing it when the page becomes visible again, and our own
     * close() — so there is one path back to "no portal", and no listener
     * left behind on a window that no longer exists.
     */
    win.addEventListener('pagehide', forget, { once: true });

    autoOpenedRef.current = auto;
    winRef.current = win;
    setPipWindow(win);
  }, [forget, toast]);

  /* ---------------------------------------------------------------- consent */

  const enableAuto = useCallback(() => {
    setAutoPref(ON);
    toast(
      'Miniplayer on. Chrome will ask once more the first time it opens by itself.',
      'success',
    );
  }, [toast]);

  /**
   * The one-time question, asked on RETURN to the tab.
   *
   * It cannot be asked at the honest moment — the user is gone by then — so
   * the trigger is "they left while something was playing" and the question
   * lands when they come back, with the thing that just happened still fresh.
   * Never on page load: nobody has context for it before they have played
   * anything.
   *
   * The answer is recorded as OFF *before* the toast goes up, so letting it
   * time out is a "no" that sticks across sessions. Asked once, ever. Missing
   * it costs nothing permanent: the button arms the same preference.
   */
  const ask = useCallback(() => {
    setAutoPref(OFF);
    toast(
      'Keep a small player on top when you switch tabs?',
      'info',
      { duration: 14000, action: { label: 'Turn on', onClick: enableAuto } },
    );
  }, [toast, enableAuto]);

  /* ------------------------------------------------------------- the action */

  /**
   * Chrome's `enterpictureinpicture`. `enterPictureInPictureReason` tells us
   * WHY, and the platform therefore draws the auto/deliberate line for us:
   * 'contentoccluded' is the tab being switched away from, 'useraction' is the
   * user hitting a browser-provided PiP control — which is a deliberate open
   * and is honoured even with the automatic behaviour switched off.
   */
  const requestPip = useCallback((details) => {
    const byUser = details?.enterPictureInPictureReason === 'useraction';
    if (!byUser) {
      if (autoRef.current !== ON) return;
      // Chrome already requires playing, audible media to fire this. Repeated
      // here because it is free, and because it also covers reason 'other'.
      if (!player.getState().playing) return;
    }
    open({ auto: !byUser });
  }, [open]);

  /*
   * Registering the handler is what makes the page ELIGIBLE, and eligibility
   * is what can make Chrome prompt. So it is registered only once the user has
   * said yes, and unregistered the moment they say no — a user who turned this
   * off must stop being eligible, not merely have the callback ignored.
   */
  useEffect(() => {
    if (!PIP_SUPPORTED || autoPref !== ON) {
      player.setPipHandler(null);
      return undefined;
    }
    player.setPipHandler(requestPip);
    return () => player.setPipHandler(null);
  }, [autoPref, requestPip]);

  /* ----------------------------------------------------------- visibility */

  useEffect(() => {
    if (!PIP_SUPPORTED) return undefined;

    const onVis = () => {
      if (document.visibilityState === 'hidden') {
        // The honest trigger for the one-time question: they just walked away
        // from something that was playing.
        if (autoRef.current === UNASKED && player.getState().playing) {
          pendingAskRef.current = true;
        }
        return;
      }

      // Back on the tab. A close within DISMISS_CONFIRM_MS of now was a return,
      // not a rejection.
      clearTimeout(dismissTimerRef.current);

      /*
       * Only a window that opened BY ITSELF is closed on return. One the user
       * opened deliberately is theirs to keep — closing it would be the app
       * arguing with an explicit choice. (Chrome also closes auto-opened
       * windows itself; this makes the behaviour ours and certain rather than
       * inherited.)
       */
      if (autoOpenedRef.current) close();

      const notice = noticeRef.current;
      if (notice) {
        noticeRef.current = null;
        toast(notice.msg, notice.kind);
      }
      if (pendingAskRef.current) {
        pendingAskRef.current = false;
        ask();
      }
    };

    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, [close, toast, ask]);

  /* ------------------------------------------------------------- the button */

  const toggle = useCallback(() => {
    if (winRef.current) { close(); return; }

    /*
     * Opening it by hand is the one gesture that unambiguously says "I want
     * this window", so it is also how the automatic behaviour is armed — and
     * the way back for someone who dismissed it or missed the prompt. It is
     * announced, because a button that silently changes a second thing is a
     * trap.
     */
    if (autoRef.current !== ON) {
      setAutoPref(ON);
      toast('Miniplayer on — it will also open by itself when you switch tabs.', 'success');
    }
    open({ auto: false });
  }, [close, open, toast]);

  // Tearing the player down (sign-out, route change) must take the window
  // with it, or an orphaned always-on-top panel outlives the session.
  useEffect(() => () => {
    clearTimeout(dismissTimerRef.current);
    const win = winRef.current;
    winRef.current = null;
    try { win?.close(); } catch { /* already gone */ }
  }, []);

  return {
    supported: PIP_SUPPORTED,
    pipWindow,
    autoEnabled: autoPref === ON,
    open,
    close,
    toggle,
  };
}
