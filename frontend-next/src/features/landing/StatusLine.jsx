/**
 * The landing page's `#loginStatus` line (frontend/login.js `setStatus`).
 *
 * Deliberately NOT ToastContext: this renders before there is a session and
 * the landing page is self-contained, so it keeps the legacy element, its
 * aria-live politeness and its "info auto-dismisses, errors stay" behaviour.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import styles from './StatusLine.module.css';

const AUTO_DISMISS_MS = 4000;

/** `[status, setStatus]` where setStatus(message, kind?) — kind: 'info' | 'error'. */
export function useStatus() {
  const [status, setStatusState] = useState(null);
  const timer = useRef(0);

  const setStatus = useCallback((message, kind = 'info') => {
    clearTimeout(timer.current);
    if (!message) {
      setStatusState(null);
      return;
    }
    setStatusState({ message, kind });
    if (kind !== 'error') {
      timer.current = setTimeout(() => setStatusState(null), AUTO_DISMISS_MS);
    }
  }, []);

  useEffect(() => () => clearTimeout(timer.current), []);

  return [status, setStatus];
}

export default function StatusLine({ status }) {
  return (
    <div
      className={`${styles.status} ${status?.kind === 'error' ? styles.error : ''}`}
      role="status"
      aria-live="polite"
      hidden={!status}
    >
      {status?.message ?? ''}
    </div>
  );
}
