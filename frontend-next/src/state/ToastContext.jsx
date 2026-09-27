/**
 * Toast notifications. Ports the behaviour of frontend/app.js:456-570 —
 * max 3 visible, 5s default, variants info/success/error/warning.
 */

import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import styles from './Toast.module.css';

const ToastCtx = createContext(null);

const MAX_VISIBLE = 3;
const DEFAULT_DURATION = 5000;
const VARIANTS = new Set(['info', 'success', 'error', 'warning']);

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(1);
  const timers = useRef(new Map());

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (message, variant = 'info', options = {}) => {
      if (!message) return () => {};
      const id = nextId.current++;
      const kind = VARIANTS.has(variant) ? variant : 'info';
      const duration = options.duration ?? DEFAULT_DURATION;

      setToasts((list) => [...list, { id, message, variant: kind, action: options.action }]
        .slice(-MAX_VISIBLE));

      if (duration > 0) {
        timers.current.set(id, setTimeout(() => dismiss(id), duration));
      }
      return () => dismiss(id);
    },
    [dismiss]
  );

  const value = useMemo(() => Object.assign(push, { dismiss }), [push, dismiss]);

  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div className={styles.container} aria-live="polite" aria-atomic="false">
        {toasts.map((t) => (
          <div key={t.id} className={`${styles.toast} ${styles[t.variant]}`} role="status">
            <span className={styles.msg}>{t.message}</span>
            {t.action && (
              <button className={styles.action} type="button" onClick={() => { t.action.onClick(); dismiss(t.id); }}>
                {t.action.label}
              </button>
            )}
            <button className={styles.close} type="button" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/** Returns a `toast(message, variant?, options?)` function. */
export function useToast() {
  const ctx = useContext(ToastCtx);
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>');
  return ctx;
}
