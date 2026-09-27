/**
 * Shared shell for the right-anchored inspector panels (metrics, debug).
 *
 * These are deliberately NOT modal — `aria-modal="false"`, no focus trap, no
 * scrim — matching the legacy panels, which stay open while you keep using the
 * player. What they do get: Escape to close, click-outside to close, and focus
 * restored to the trigger on close.
 */

import { useEffect, useRef } from 'react';
import styles from './Panel.module.css';

export default function Panel({ open, onClose, title, id, children, wide }) {
  const ref = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (!open) return undefined;
    const previouslyFocused = document.activeElement;

    const onKeyDown = (ev) => {
      if (ev.key === 'Escape') {
        ev.preventDefault();
        closeRef.current?.();
      }
    };
    // mousedown, not click: matches legacy and avoids closing on a drag that
    // started inside the panel and ended outside it.
    const onPointerDown = (ev) => {
      if (ref.current && !ref.current.contains(ev.target)) closeRef.current?.();
    };

    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('mousedown', onPointerDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('mousedown', onPointerDown);
      if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
        previouslyFocused.focus({ preventScroll: true });
      }
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className={`${styles.panel} ${wide ? styles.wide : ''}`}
      role="dialog"
      aria-modal="false"
      aria-labelledby={`${id}Title`}
      ref={ref}
    >
      <div className={styles.header}>
        <h2 className={styles.title} id={`${id}Title`}>
          {title}
        </h2>
        <button
          className={styles.close}
          type="button"
          aria-label={`Close ${title.toLowerCase()}`}
          onClick={onClose}
        >
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <div className={styles.body}>{children}</div>
    </div>
  );
}
