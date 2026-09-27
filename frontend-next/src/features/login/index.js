/**
 * Public surface of the login feature.
 *
 *   import AuthGate, { useAuth } from './features/login';
 *
 * AuthGate mounts AuthProvider itself, so App.jsx only needs the one wrapper.
 * Everything below it can call useAuth() for { user, isLoading, signOut }.
 */

export { default } from './AuthGate';
export { default as AuthGate } from './AuthGate';
export { default as AuthPanel } from './AuthPanel';
export { useAuth, AuthProvider } from '../../state/AuthContext';
