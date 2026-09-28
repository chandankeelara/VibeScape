/**
 * Wrap protected content in this:
 *
 *   <AuthGate><App /></AuthGate>
 *
 * No session -> the landing page, which carries the auth modal.
 * Session     -> children, with `useAuth()` available anywhere below.
 *
 * Replaces the legacy show/hideAuthOverlay + `body.auth-locked` visibility
 * hack (frontend/style.css:2997-3004): React just doesn't render the app until
 * there's a user, so there's nothing to hide.
 */

import { AuthProvider, useAuth } from '../../state/AuthContext';
import styles from './Auth.module.css';
import { Spinner } from './parts';
import LandingPage from '../landing';

function Gate({ children, fallback }) {
  const { user, isLoading, completeLogin } = useAuth();

  // Hydrating a stored token (legacy `hydrateSessionFromStorage`). Rendering
  // the auth card here would flash it at every reload for a signed-in user.
  if (isLoading) {
    return (
      fallback ?? (
        <div className={styles.boot}>
          <Spinner label="Restoring your session…" />
        </div>
      )
    );
  }

  // Signed out = the marketing landing page, which carries its own auth
  // modal (Spotify / email / guest). It reads completeLogin from useAuth
  // itself; passing it keeps the dependency explicit.
  if (!user) return <LandingPage onAuthenticated={completeLogin} />;

  return children;
}

export default function AuthGate({ children, fallback }) {
  return (
    <AuthProvider>
      <Gate fallback={fallback}>{children}</Gate>
    </AuthProvider>
  );
}
