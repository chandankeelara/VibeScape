/**
 * Email + password sign-in / account creation.
 *
 * This is the form the backend actually implements: /api/auth/login and
 * /api/auth/signup take { email, password } (backend/app.py:309-400). The
 * legacy in-app overlay's create view collected a display name + optional
 * 4-digit PIN, which no longer matches any route — see the report / the note
 * in the (now removed) AuthPanel. The field chrome, the reveal-confirm-on-type behaviour and
 * the inline error styling are ported from that view; the *fields* follow the
 * backend contract and the landing page (frontend/login.js `onEmailSubmit`).
 */

import { useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';

import * as api from '../../lib/api';
import { useToast } from '../../state/ToastContext';
import styles from './Auth.module.css';
import { BackButton } from './parts';

/** Error codes from backend/app.py, messages from frontend/login.js:302-310. */
const MESSAGES = {
  email_taken: 'That email is already registered — try signing in.',
  invalid_email: "That doesn't look like a valid email.",
  password_too_short: 'Password must be at least 6 characters.',
  password_required: 'Enter your password.',
  bad_credentials: 'Wrong email or password.',
};

const MIN_PASSWORD = 6;

function messageFor(error, mode) {
  const code = error?.body?.detail?.error;
  if (code && MESSAGES[code]) return MESSAGES[code];
  if (error?.status === 401) return MESSAGES.bad_credentials;
  if (error?.status === 409) return MESSAGES.email_taken;
  return mode === 'create' ? 'Could not create account. Try again.' : 'Could not sign in. Check the backend.';
}

export default function EmailForm({ mode, onBack, onSuccess }) {
  const isCreate = mode === 'create';
  const toast = useToast();
  const emailRef = useRef(null);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [badField, setBadField] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    emailRef.current?.focus();
  }, [mode]);

  const submit = useMutation({
    mutationFn: (body) => (isCreate ? api.signup(body) : api.login(body)),
    onSuccess: (payload) => {
      toast(
        isCreate
          ? `Welcome, ${payload?.display_name || email}.`
          : `Signed in as ${payload?.display_name || email}.`,
        'success'
      );
      onSuccess(payload);
    },
    onError: (err) => {
      const msg = messageFor(err, mode);
      setError(msg);
      setBadField(err?.status === 401 ? 'password' : 'email');
      toast(msg, 'error');
    },
  });

  function handleSubmit(ev) {
    ev.preventDefault();
    setError('');
    setBadField(null);

    if (!email.trim()) {
      setBadField('email');
      setError('Enter your email address.');
      return;
    }
    if (!password) {
      setBadField('password');
      setError(MESSAGES.password_required);
      return;
    }
    if (isCreate) {
      if (password.length < MIN_PASSWORD) {
        setBadField('password');
        setError(MESSAGES.password_too_short);
        return;
      }
      if (confirm !== password) {
        setBadField('confirm');
        setError('Passwords do not match.');
        return;
      }
    }
    submit.mutate({ email: email.trim(), password });
  }

  const cls = (field) => `${styles.input} ${badField === field ? styles.inputError : ''}`;

  return (
    <div className={styles.view}>
      <BackButton onClick={onBack} />
      <div className={styles.formBody}>
        <p className={styles.hint}>
          {isCreate
            ? 'Add a new profile to VibeScape. Each profile has its own library and Spotify link.'
            : 'Sign in with the email and password you created your profile with.'}
        </p>
        <form className={styles.form} onSubmit={handleSubmit} noValidate>
          <label className={styles.field}>
            <span className={styles.fieldLabel}>Email</span>
            <input
              ref={emailRef}
              className={cls('email')}
              type="email"
              name="email"
              autoComplete="email"
              spellCheck="false"
              autoCapitalize="none"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              aria-invalid={badField === 'email' || undefined}
            />
          </label>

          <label className={styles.field}>
            <span className={styles.fieldLabel}>
              {isCreate ? `Password (min ${MIN_PASSWORD} characters)` : 'Password'}
            </span>
            <input
              className={cls('password')}
              type="password"
              name="password"
              autoComplete={isCreate ? 'new-password' : 'current-password'}
              placeholder={isCreate ? 'Choose a password' : 'Your password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={badField === 'password' || undefined}
            />
          </label>

          {/* Ported behaviour: the confirm field only appears once you start
              typing — legacy did this for the optional PIN. */}
          {isCreate && password.length > 0 && (
            <label className={styles.field}>
              <span className={styles.fieldLabel}>Confirm password</span>
              <input
                className={cls('confirm')}
                type="password"
                name="confirmPassword"
                autoComplete="new-password"
                placeholder="Re-enter password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                aria-invalid={badField === 'confirm' || undefined}
              />
            </label>
          )}

          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}

          <button
            className={`${styles.btn} ${styles.btnPrimary}`}
            type="submit"
            disabled={submit.isPending}
          >
            {submit.isPending
              ? isCreate
                ? 'Creating…'
                : 'Signing in…'
              : isCreate
                ? 'Create profile'
                : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  );
}
