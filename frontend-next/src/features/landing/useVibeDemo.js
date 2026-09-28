/**
 * The hero's live vibe demo — the signature moment of the landing page.
 *
 * Ports frontend/login.js `animateMoodGrid`, with three deliberate changes:
 *
 *   1. The accent colour is returned, not written to `document.documentElement`.
 *      Legacy set `--accent` globally; here the caller puts it on the landing
 *      page's own root element so the rest of the app (and the auth flows
 *      rendered over it) are never mutated by a marketing page.
 *   2. The auto-drift is a rAF loop with a real delta-time instead of a
 *      `setInterval(…, 33)`, so a backgrounded tab costs nothing and the sweep
 *      speed doesn't depend on timer drift. Same 18 units/second as legacy
 *      (0.6 per 33 ms), same 8..92 turnaround, same "stop the moment the
 *      visitor touches it, pause while they hover".
 *   3. `prefers-reduced-motion` is observed live rather than sampled once.
 *
 * State updates are throttled to integer vibe changes, and every section below
 * the hero is memoised, so a drifting slider only re-renders the hero.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { clamp, colorFor, DEMOS, moodFor } from './vibe';

/** Units of vibe per second while drifting — legacy: 0.6 per 33 ms. */
const DRIFT_RATE = 18;
const DRIFT_TOP = 92;
const DRIFT_BOTTOM = 8;

const START_VIBE = 42;

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  );
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (!mq) return undefined;
    const onChange = (e) => setReduced(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return reduced;
}

export function useVibeDemo() {
  const [vibe, setVibeState] = useState(START_VIBE);
  const [touched, setTouched] = useState(false);
  const [cursorY, setCursorY] = useState(0);

  const vibeRef = useRef(START_VIBE);
  const hoverRef = useRef(false);
  const sliderRef = useRef(null);
  const trackRef = useRef(null);
  const gridRef = useRef(null);
  const activeCellRef = useRef(null);

  const reduced = usePrefersReducedMotion();

  const mood = moodFor(vibe);
  const accent = colorFor(vibe);
  const demo = DEMOS[mood] ?? DEMOS.chill;

  /** Called by the range input and the demo's own arrow-key handling. */
  const setVibe = useCallback((next) => {
    const v = clamp(Math.round(next), 0, 100);
    vibeRef.current = v;
    setVibeState(v);
    setTouched(true);
  }, []);

  const nudge = useCallback((delta) => setVibe(vibeRef.current + delta), [setVibe]);

  const hoverProps = useMemo(
    () => ({
      onPointerEnter: () => {
        hoverRef.current = true;
      },
      onPointerLeave: () => {
        hoverRef.current = false;
      },
    }),
    []
  );

  // ---- Auto-drift. Stops for good once the visitor grabs the slider.
  useEffect(() => {
    if (reduced || touched) return undefined;

    let raf = 0;
    let last = performance.now();
    let v = vibeRef.current;
    let dir = 1;

    const tick = (now) => {
      // Cap the delta so a tab that was backgrounded doesn't teleport.
      const dt = Math.min(64, now - last) / 1000;
      last = now;
      if (!hoverRef.current) {
        v += dir * DRIFT_RATE * dt;
        if (v >= DRIFT_TOP) {
          v = DRIFT_TOP;
          dir = -1;
        }
        if (v <= DRIFT_BOTTOM) {
          v = DRIFT_BOTTOM;
          dir = 1;
        }
        const rounded = Math.round(v);
        if (rounded !== vibeRef.current) {
          vibeRef.current = rounded;
          setVibeState(rounded);
        }
      }
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [reduced, touched]);

  // ---- The vertical slider is a rotated horizontal range input, so its
  // pre-rotation *width* has to track the wrapper's *height* or the thumb
  // travel stops lining up with the five mood cells. Legacy did this on load
  // + resize + ResizeObserver; the observer alone covers all three.
  useLayoutEffect(() => {
    const track = trackRef.current;
    const slider = sliderRef.current;
    if (!track || !slider || typeof ResizeObserver !== 'function') return undefined;

    const sync = () => {
      const h = track.getBoundingClientRect().height;
      if (h > 40) slider.style.setProperty('--slider-len', `${h}px`);
    };
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(track);
    return () => ro.disconnect();
  }, []);

  // ---- Park the glow puck against the active mood cell. Measured rather
  // than computed because the ladder rows are `1fr` inside a grid whose
  // height is set by the hero card.
  useLayoutEffect(() => {
    const measure = () => {
      const cell = activeCellRef.current;
      const grid = gridRef.current;
      if (!cell || !grid) return;
      const g = grid.getBoundingClientRect();
      const r = cell.getBoundingClientRect();
      setCursorY(r.top - g.top + r.height / 2 - 11);
    };
    measure();
    if (typeof ResizeObserver !== 'function' || !gridRef.current) return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(gridRef.current);
    return () => ro.disconnect();
  }, [mood]);

  return {
    vibe,
    mood,
    accent,
    demo,
    setVibe,
    nudge,
    hoverProps,
    cursorY,
    refs: { sliderRef, trackRef, gridRef, activeCellRef },
  };
}
