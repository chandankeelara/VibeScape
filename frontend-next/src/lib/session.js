/**
 * Session token storage.
 *
 * Deliberately uses the SAME localStorage key as the legacy app
 * (`vibescape_session_token`). Both apps are served from the same origin, so
 * a user signed in at / is already signed in at /next and vice versa. That is
 * what makes the strangler-fig migration seamless — do not rename this key
 * until the legacy app is retired.
 */

export const SESSION_KEY = 'vibescape_session_token';

const listeners = new Set();

export function getToken() {
  try {
    return localStorage.getItem(SESSION_KEY) || '';
  } catch {
    return '';
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(SESSION_KEY, token);
    else localStorage.removeItem(SESSION_KEY);
  } catch {
    /* private mode / storage disabled — session is in-memory only */
  }
  listeners.forEach((fn) => fn(token || ''));
}

export function clearToken() {
  setToken('');
}

/** Subscribe to token changes in this tab; returns an unsubscribe fn. */
export function onTokenChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
