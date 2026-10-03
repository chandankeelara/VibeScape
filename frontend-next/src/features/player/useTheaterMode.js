import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * YouTube-style theater mode: a LAYOUT toggle, not a visual redesign.
 *
 * Two values, deliberately separate:
 *
 *   `preferred` — what the user asked for, persisted. Survives track changes,
 *                 mode changes and reloads, exactly like YouTube's own.
 *   `theater`   — whether it is in effect right now, which also requires
 *                 video mode. Switching to audio therefore drops the layout
 *                 but REMEMBERS the choice; switching back restores it.
 *
 * The viewport guard IS here, and it did not used to be.
 *
 * While theater was only a restyle, CSS media queries covered it for free and
 * a matchMedia subscription would have been dead weight. Now theater also
 * SWAPS THE DOM — the meta column is replaced by TheaterBar — and CSS cannot
 * undo that. Below 1024px the control is display:none, so a user who had
 * theater on, narrowed the window and lost the mood slider would have had no
 * way to get it back. The guard is a `change` listener on one media query, so
 * it fires on crossing the breakpoint and not on every resize tick.
 *
 * `morphing` is a transient flag that drives the settle animation. The layout
 * itself flips in one discrete step — see PlayerPage.module.css for why
 * nothing animates grid-template-columns.
 */

const STORAGE_KEY = 'vs.player.theater';

/** Must match the @media gates in PlayerPage/ArtStage.module.css. */
const WIDE = '(min-width: 1024px)';

/** Must outlast the settle keyframe (--dur-slow, 420ms). */
const MORPH_MS = 460;

function load() {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

/** True while the viewport is wide enough for theater to mean anything. */
function useWideViewport() {
  const [wide, setWide] = useState(
    () => (typeof window !== 'undefined' && window.matchMedia
      ? window.matchMedia(WIDE).matches
      : true),
  );

  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const onChange = (e) => setWide(e.matches);
    // Re-read once: the breakpoint can be crossed between first render and
    // this effect (and StrictMode's double-mount makes that observable).
    setWide(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  return wide;
}

export default function useTheaterMode(available) {
  const [preferred, setPreferred] = useState(load);
  const [morphing, setMorphing] = useState(false);
  const timerRef = useRef(0);
  const wide = useWideViewport();

  const theater = !!available && wide && preferred;

  // Persisted in an effect rather than inside the state updater: StrictMode
  // double-invokes updaters, and an updater that writes to storage is a side
  // effect in a place React is allowed to run twice.
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, preferred ? '1' : '0');
    } catch {
      /* storage off (private mode / blocked) — the toggle still works, it
         just won't be remembered. */
    }
  }, [preferred]);

  const toggleTheater = useCallback(() => setPreferred((v) => !v), []);

  /*
   * Leaves theater WITHOUT raising `morphing`, and this is load-bearing.
   *
   * The only caller is the detach gesture (and the one-shot boot guard in
   * ArtStage). The settle keyframe puts a `transform` on every stage child,
   * including the ArtStage section that CONTAINS the video frame — and a
   * transformed ancestor becomes the containing block for position:fixed
   * descendants. Detaching while that animation ran would re-anchor the
   * frame the user is actively dragging to the stage column for 460ms, so it
   * would leap out from under the pointer and snap back.
   *
   * It is also simply the right behaviour: the frame is popping out under
   * the cursor, which is motion enough.
   */
  const theaterRef = useRef(theater);
  theaterRef.current = theater;
  const skipMorphRef = useRef(false);
  const exitTheater = useCallback(() => {
    // Guarded so a detach that wasn't leaving theater cannot leave the skip
    // flag armed and swallow the next real toggle.
    if (!theaterRef.current) return;
    skipMorphRef.current = true;
    setPreferred(false);
  }, []);

  // Raise `morphing` on every effective change, including the one caused by
  // leaving video mode, so the stage always settles rather than snapping.
  const firstRef = useRef(true);
  useEffect(() => {
    if (firstRef.current) {
      firstRef.current = false;
      return undefined;
    }
    if (skipMorphRef.current) {
      skipMorphRef.current = false;
      return undefined;
    }
    setMorphing(true);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setMorphing(false), MORPH_MS);
    return undefined;
  }, [theater]);

  useEffect(() => () => clearTimeout(timerRef.current), []);

  return { theater, morphing, toggleTheater, exitTheater };
}
