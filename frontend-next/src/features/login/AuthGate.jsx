/**
 * Wrap protected content in this:
 *
 *   <AuthGate><App /></AuthGate>
 *
 * No session -> the auth card (Spotify / guest / email / create).
 * Session     -> children, with `useAuth()` available anywhere below.
 *
 * Replaces the legacy show/hideAuthOverlay + `body.auth-locked` visibility
 * hack (frontend/style.css:2997-3004): React just doesn't render the app until
 * there's a user, so there's nothing to hide.
 */

import { AuthProvider, useAuth } from '../../state/AuthContext';
import styles from './Auth.module.css';
import { Spinner } from './parts';
import AuthPanel from './AuthPanel';

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

  if (!user) return <AuthPanel onAuthenticated={completeLogin} />;

  return children;
}

export default function AuthGate({ children, fallback }) {
  return (
    <AuthProvider>
      <Gate fallback={fallback}>{children}</Gate>
    </AuthProvider>
  );
}
