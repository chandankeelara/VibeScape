/**
 * Public surface of the landing feature.
 *
 *   import LandingPage from './features/landing';
 *
 * Renders standalone with no session, but must be mounted inside
 * <AuthProvider> (i.e. inside AuthGate) because it reads `completeLogin` from
 * AuthContext. Pass `onAuthenticated` to intercept the payload instead.
 */

export { default } from './LandingPage';
export { default as LandingPage } from './LandingPage';
