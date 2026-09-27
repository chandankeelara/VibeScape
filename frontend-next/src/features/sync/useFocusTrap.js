/**
 * Focus trap + Escape handling for modal dialogs.
 *
 * The legacy modal did this by hand across several listeners; this folds it
 * into one hook. Behaviour it preserves:
 *   - focus moves into the dialog on open
 *   - Tab / Shift+Tab cycle within the dialog
 *   - Escape closes (the caller decides what "close" means — the sync modal
 *     cancels a running job first)
 *   - focus returns to whatever was focused before open
 *
 * `onEscape` is read through a ref so the listener doesn't churn every render.
 */

import { useEffect, useRef } from 'react';

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function useFocusTrap(open, onEscape) {
  const ref = useRef(null);
  const escRef = useRef(onEscape);
  escRef.current = onEscape;

  useEffect(() => {
    if (!open) return undefined;
    const node = ref.current;
    if (!node) return undefined;

    const previouslyFocused = document.activeElement;

    // Defer one frame: on the first open the stage content may still be
    // mounting, and focusing a node that is about to move jumps the scroll.
    const raf = requestAnimationFrame(() => {
      const first = node.querySelector(FOCUSABLE);
      (first || node).focus({ preventScroll: true });
    });

    const onKeyDown = (ev) => {
      if (ev.key === 'Escape') {
        ev.preventDefault();
        escRef.current?.();
        return;
      }
      if (ev.key !== 'Tab') return;
      const items = Array.from(node.querySelectorAll(FOCUSABLE)).filter(
        (n) => n.offsetParent !== null || n === document.activeElement
      );
      if (!items.length) {
        ev.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (ev.shiftKey && document.activeElement === first) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && document.activeElement === last) {
        ev.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown, true);
    document.body.classList.add('modal-open');

    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener('keydown', onKeyDown, true);
      document.body.classList.remove('modal-open');
      if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
        previouslyFocused.focus({ preventScroll: true });
      }
    };
  }, [open]);

  return ref;
}
