/**
 * Spotify debug panel — port of frontend/app.js:3577-3694 and
 * index.html:186-250.
 *
 * Shows the OAuth token, its scopes and expiry, a live "test /v1/me" probe,
 * and the current track's classification source / URL / headline features.
 *
 * DEV ONLY. The legacy app gated this behind `body[data-env="dev"]`; here it
 * returns null unless /api/client-config reports env=dev. The gate is a render
 * gate rather than a CSS one on purpose — this panel prints a bearer token, so
 * "hidden but present in the DOM" is not good enough.
 */

import { useCallback, useState } from 'react';

import { usePlayer } from '../../state/PlayerContext';
import { useToast } from '../../state/ToastContext';
import { useSpotifyAuth } from '../../state/SpotifyAuthContext';
import Panel from './Panel';
import { useDevEnv } from './useDevEnv';
import { classificationLabel, fmtMetricValue, useTrackFeatures } from './features';
import styles from './DebugPanel.module.css';

/** "in 42m 10s (3:14:00 PM)" / "expired" / "—". Ported from app.js:3581. */
export function fmtRelativeExpiry(ms) {
  if (!ms) return '—';
  const delta = ms - Date.now();
  if (delta <= 0) return 'expired';
  const totalSec = Math.floor(delta / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const abs = new Date(ms).toLocaleTimeString();
  if (h > 0) return `in ${h}h ${m}m (${abs})`;
  if (m > 0) return `in ${m}m ${s}s (${abs})`;
  return `in ${s}s (${abs})`;
}

function Feat({ label, value, decimals = 2, unit }) {
  const shown = fmtMetricValue(value, decimals);
  return (
    <div>
      <span className={styles.featLabel}>{label}</span>
      <span className={shown === null ? `${styles.featVal} ${styles.null}` : styles.featVal}>
        {shown === null ? '—' : unit ? `${shown} ${unit}` : shown}
      </span>
    </div>
  );
}

function DebugBody({ authMode }) {
  const toast = useToast();
  const { current } = usePlayer();
  const { token, profile, scope, scopeVersion, expiresAt } = useSpotifyAuth();
  const [output, setOutput] = useState('Click "test /v1/me" to verify the token.');
  const [testing, setTesting] = useState(false);

  // Cached-only: the debug panel piggybacks on whatever the metrics panel
  // already fetched rather than triggering its own request.
  const { data: features } = useTrackFeatures(current, { enabled: false });
  const src = features || current || {};

  const copy = useCallback(
    async (text, successMsg) => {
      if (!text) {
        toast('Nothing to copy.', 'info');
        return;
      }
      try {
        await navigator.clipboard.writeText(text);
        toast(successMsg || 'Copied', 'success');
      } catch {
        toast('Copy failed — select manually.', 'warning');
      }
    },
    [toast]
  );

  const testMe = useCallback(async () => {
    if (!token) return;
    setTesting(true);
    setOutput('Fetching…');
    try {
      // Deliberate bare fetch: api.js owns the VibeScape backend, and this
      // call goes straight to Spotify's own API with the OAuth token. Routing
      // it through api.js would mean inventing a backend proxy that only the
      // debug panel uses. This is the single documented exception.
      const r = await fetch('https://api.spotify.com/v1/me', {
        headers: { Authorization: `Bearer ${token}` },
      });
      const text = await r.text();
      let pretty = text;
      try {
        pretty = JSON.stringify(JSON.parse(text), null, 2);
      } catch {
        /* non-JSON body — show it raw */
      }
      setOutput(`HTTP ${r.status}\n\n${pretty}`);
    } catch (e) {
      setOutput(`Request error: ${e?.message || String(e)}`);
    } finally {
      setTesting(false);
    }
  }, [token]);

  const authModeSuffix = authMode ? ` · ingest: ${authMode}` : '';
  const user = profile?.display_name
    ? `${profile.display_name}${profile.product === 'premium' ? ' · premium' : ' · free'}${authModeSuffix}`
    : `(not signed in)${authModeSuffix}`;

  return (
    <>
      <div className={styles.row}>
        <div className={styles.label}>User</div>
        <div className={styles.value}>{user}</div>
      </div>

      <div className={styles.row}>
        <div className={styles.label}>Expires</div>
        <div className={styles.value}>{fmtRelativeExpiry(expiresAt)}</div>
      </div>

      <div className={styles.row}>
        <div className={styles.label}>Scopes{scopeVersion != null ? ` (v${scopeVersion})` : ''}</div>
        <div className={`${styles.value} ${styles.scopes}`}>
          {(scope || '').trim() ? scope.trim().split(/\s+/).join('\n') : '—'}
        </div>
      </div>

      <div className={styles.block}>
        <div className={styles.labelInline}>
          <span>Access token</span>
          <button
            className={styles.miniBtn}
            type="button"
            onClick={() => copy(token, 'Token copied')}
          >
            copy
          </button>
        </div>
        <pre className={styles.token} tabIndex={0}>
          {token || '(no token)'}
        </pre>
      </div>

      <div className={styles.block}>
        <div className={styles.labelInline}>
          <span>Response</span>
          <button className={styles.miniBtn} type="button" onClick={testMe} disabled={!token || testing}>
            test /v1/me
          </button>
        </div>
        <pre className={styles.output} aria-live="polite">
          {output}
        </pre>
      </div>

      <div className={styles.sectionTitle}>Current track classification</div>

      <div className={styles.row}>
        <div className={styles.label}>Source</div>
        <div className={styles.value}>
          {!current
            ? '(no track loaded)'
            : `${current.classification_source || 'unknown'} — ${classificationLabel(current.classification_source)}`}
        </div>
      </div>

      <div className={styles.block}>
        <div className={styles.labelInline}>
          <span>Classification URL</span>
          <button
            className={styles.miniBtn}
            type="button"
            onClick={() =>
              current?.preview_url
                ? copy(current.preview_url, 'Classification URL copied')
                : toast('No classification URL to copy.', 'info')
            }
          >
            copy
          </button>
        </div>
        <pre className={styles.token} tabIndex={0}>
          {current?.preview_url || '(no url)'}
        </pre>
      </div>

      <div className={styles.sectionTitle}>Current track features</div>
      <div className={styles.featGrid}>
        <Feat label="activation" value={current ? src.activation : null} decimals={1} />
        <Feat label="valence" value={current ? src.valence : null} decimals={1} />
        <Feat label="acousticness" value={current ? src.acousticness : null} decimals={1} />
        <Feat label="tempo" value={current ? src.tempo : null} decimals={0} unit="BPM" />
        <Feat label="energy" value={current ? src.energy_mean : null} decimals={3} />
      </div>

      <p className={styles.note}>Personal debug view — don’t paste tokens publicly.</p>
    </>
  );
}

/**
 * `authMode` is the last-seen ingest auth mode ('user_oauth' | 'app_token')
 * from a sync job status poll. The legacy app stashed it on the sync state and
 * re-rendered this panel when it changed; here the parent passes it down if it
 * has one. Optional.
 */
export default function DebugPanel({ open, onClose, authMode }) {
  const isDev = useDevEnv();
  if (!isDev) return null;

  return (
    <Panel open={open} onClose={onClose} title="Spotify debug" id="debugPanel">
      <DebugBody authMode={authMode} />
    </Panel>
  );
}
