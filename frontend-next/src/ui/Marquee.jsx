import { useLayoutEffect, useRef } from 'react';
import styles from './Marquee.module.css';

/**
 * One line of text in a fixed box. If it fits, it is still. If it does not,
 * it slides to its end, rests, slides back, rests, and repeats — slowly
 * enough to read while it moves.
 *
 * The motion is a Web Animation on the inner span (transform only, on the
 * compositor), rebuilt only when the overflow changes: on resize, on a font
 * landing, on a new text. Nothing re-renders per frame, and the box never
 * changes size, so a title that scrolls moves nothing around it.
 *
 * The full text is always in the DOM (a transform does not hide it from
 * assistive tech) and in the tooltip. Hovering the line, or focusing the
 * control it sits in, holds it where it is. Under reduced motion it never
 * moves: the line ends in an ellipsis instead (Marquee.module.css).
 */

// Average speed of a slide. 34px/s reads as drifting, not scrolling; the
// eased slide peaks at about 1.6x that, mid-travel.
export const MARQUEE_SPEED = 34;
export const MARQUEE_REST_MS = 1500;
// The shortest slide: a title a few pixels too long should not twitch.
const MIN_SLIDE_MS = 900;
const EASE = 'cubic-bezier(0.45, 0, 0.55, 1)';
const REDUCE = '(prefers-reduced-motion: reduce)';

/** Keyboard focus, where the browser can tell; any focus where it cannot. */
function focusVisible(el) {
  try {
    return el.matches(':focus-visible');
  } catch {
    return true;
  }
}

export default function Marquee({ text, className = '' }) {
  const rootRef = useRef(null);
  const textRef = useRef(null);

  useLayoutEffect(() => {
    const root = rootRef.current;
    const span = textRef.current;
    if (!root || !span) return undefined;
    const reduce = window.matchMedia ? window.matchMedia(REDUCE) : null;
    // A title inside a button (the player bar) pauses when the BUTTON has
    // focus, not just when a pointer is over the line.
    const host = root.parentElement?.closest('button, a, [tabindex]') || root;
    let anim = null;
    let shift = 0;
    // Hover and keyboard focus each hold it; both must let go. Focus left
    // behind by a mouse click is not a reader parked on the line, so it does
    // not count (a clicked bar title would otherwise freeze until the next
    // click elsewhere).
    let hovered = false;
    let focused = false;
    const held = () => hovered || focused;

    const build = () => {
      const cs = getComputedStyle(root);
      // The leading padding IS the fade width (Marquee.module.css).
      const fade = parseFloat(cs.paddingLeft) || 0;
      const box = root.clientWidth - fade - (parseFloat(cs.paddingRight) || 0);
      const over = box > 0 && span.scrollWidth - box > 1;
      // Travel far enough that the last glyph comes to rest clear of the
      // trailing fade, not under it.
      const next = over && !reduce?.matches ? Math.ceil(span.scrollWidth - box + fade) : 0;
      if (over) root.dataset.over = ''; else delete root.dataset.over;
      if (next === shift && (anim || !next)) return;
      anim?.cancel();
      anim = null;
      shift = next;
      if (!next || typeof span.animate !== 'function') return;
      const slide = Math.max(MIN_SLIDE_MS, (next / MARQUEE_SPEED) * 1000);
      const total = 2 * (slide + MARQUEE_REST_MS);
      const end = `translateX(${-next}px)`;
      anim = span.animate([
        { transform: 'translateX(0)', offset: 0 },
        { transform: 'translateX(0)', offset: MARQUEE_REST_MS / total, easing: EASE },
        { transform: end, offset: (MARQUEE_REST_MS + slide) / total },
        { transform: end, offset: (2 * MARQUEE_REST_MS + slide) / total, easing: EASE },
        { transform: 'translateX(0)', offset: 1 },
      ], { duration: total, iterations: Infinity });
      if (held()) anim.pause();
    };

    const sync = () => { if (held()) anim?.pause(); else anim?.play(); };
    const onEnter = () => { hovered = true; sync(); };
    const onLeave = () => { hovered = false; sync(); };
    const onFocus = (e) => { focused = focusVisible(e.target); sync(); };
    const onBlur = () => { focused = false; sync(); };
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(build) : null;
    ro?.observe(root);
    ro?.observe(span);
    reduce?.addEventListener?.('change', build);
    host.addEventListener('pointerenter', onEnter);
    host.addEventListener('pointerleave', onLeave);
    host.addEventListener('focusin', onFocus);
    host.addEventListener('focusout', onBlur);
    build();
    return () => {
      anim?.cancel();
      ro?.disconnect();
      reduce?.removeEventListener?.('change', build);
      host.removeEventListener('pointerenter', onEnter);
      host.removeEventListener('pointerleave', onLeave);
      host.removeEventListener('focusin', onFocus);
      host.removeEventListener('focusout', onBlur);
      delete root.dataset.over;
    };
  }, [text]);

  return (
    <span ref={rootRef} className={`${styles.marquee} ${className}`} title={text || undefined}>
      <span ref={textRef} className={styles.text}>{text}</span>
    </span>
  );
}
