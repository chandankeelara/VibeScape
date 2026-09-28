/**
 * Public surface of the login feature.
 *
 *   import AuthGate, { useAuth } from './features/login';
 *
 * AuthGate mounts AuthProvider itself, so App.jsx only needs the one wrapper.
 * Signed out it renders the landing page (features/landing), which carries
 * the auth modal — the old standalone AuthPanel is gone.
 * Everything below it can call useAuth() for { user, isLoading, signOut }.
 */

export { default } from './AuthGate';
export { default as AuthGate } from './AuthGate';
export { useAuth, AuthProvider } from '../../state/AuthContext';
