/**
 * Track metrics panel — port of frontend/app.js:3754-4173 (renderMetricsPanel)
 * and index.html:253-270.
 *
 * Shows the stored ML feature blob for the current track, grouped: ML
 * predictions, derived axes, rhythm, energy, timbre, harmony, then MFCC and
 * chroma as small bar charts. The legacy version built the whole thing as
 * detached DOM nodes; here every group is JSX and the two charts are plain
 * inline <svg>.
 *
 * The bar scales are NOT decorative — each one encodes the natural range of
 * its field (tempo to 200 BPM, spectral values to 5-8 kHz, valence_mode signed
 * over -1..+1). They're carried over exactly; changing one silently re-reads
 * every value drawn against it.
 */

import { usePlayer } from '../../state/PlayerContext';
import Panel from './Panel';
import {
  featureGetter,
  fmtMetricValue,
  pct01,
  pctMax,
  useTrackFeatures,
} from './features';
import styles from './MetricsPanel.module.css';

const CHROMA_LABELS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

function Row({ label, value, decimals = 2, unit, annot, bar = true, barMax, barSigned }) {
  const shown = fmtMetricValue(value, decimals);
  const fill =
    barMax !== undefined
      ? pctMax(value, barMax, { signed: !!barSigned })
      : pct01(typeof value === 'number' ? value : null);

  return (
    <div className={bar ? styles.row : `${styles.row} ${styles.rowSimple}`}>
      <span className={styles.key}>{label}</span>
      {bar && (
        <div className={styles.bar}>
          <div className={styles.barFill} style={{ width: `${fill === null ? 0 : fill}%` }} />
        </div>
      )}
      <span className={shown === null ? `${styles.value} ${styles.valueNull}` : styles.value}>
        {shown === null ? '—' : unit ? `${shown} ${unit}` : shown}
      </span>
      {annot && <span className={styles.annot}>{annot}</span>}
    </div>
  );
}

function Group({ title, children }) {
  return (
    <div className={styles.group}>
      <div className={styles.groupTitle}>{title}</div>
      {children}
    </div>
  );
}

/**
 * Small bar chart for the MFCC / chroma vectors. Signed data (MFCC) gets a
 * zero line and centres on it; unsigned data (chroma) sits on the baseline
 * with an optional half-value reference tick.
 */
function MiniBarChart({ values, ariaLabel, midTick }) {
  if (!Array.isArray(values) || !values.length) return null;

  const w = 280;
  const h = 60;
  const gap = 2;
  const n = values.length;
  const barW = Math.max(1, (w - gap * (n - 1)) / n);
  const hasNeg = values.some((v) => typeof v === 'number' && v < 0);
  let maxAbs = 1;
  for (const v of values) {
    if (typeof v === 'number' && Number.isFinite(v)) maxAbs = Math.max(maxAbs, Math.abs(v));
  }
  const midY = hasNeg ? h / 2 : h;

  return (
    <svg
      className={styles.chart}
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={ariaLabel}
    >
      {hasNeg && (
        <line x1="0" x2={w} y1={midY} y2={midY} stroke="rgba(255,255,255,0.08)" strokeWidth="1" />
      )}
      {!hasNeg && midTick && (
        <line
          x1="0"
          x2={w}
          y1={h * 0.5}
          y2={h * 0.5}
          stroke="rgba(255,255,255,0.06)"
          strokeWidth="1"
          strokeDasharray="2 3"
        />
      )}
      {values.map((v, i) => {
        if (typeof v !== 'number' || !Number.isFinite(v)) return null;
        const scaled = (v / maxAbs) * (hasNeg ? h / 2 - 2 : h - 2);
        let y;
        let barH;
        if (hasNeg) {
          if (scaled >= 0) {
            y = midY - scaled;
            barH = scaled;
          } else {
            y = midY;
            barH = -scaled;
          }
        } else {
          y = h - scaled;
          barH = scaled;
        }
        return (
          <rect
            /* eslint-disable-next-line react/no-array-index-key -- index IS the
               identity here: bar i is coefficient i / pitch class i. */
            key={i}
            x={i * (barW + gap)}
            y={y}
            width={barW}
            height={Math.max(0.5, barH)}
            rx="1"
            fill="var(--vibe-accent)"
          />
        );
      })}
    </svg>
  );
}

function MetricsBody({ track }) {
  // A track dict that already carries derived axes renders immediately; the
  // fetch then fills in mfcc/chroma/ML. This is the legacy "hasInlineDerived"
  // fast path, minus its hand-rolled cache.
  const hasInlineDerived =
    (track.activation !== null && track.activation !== undefined) ||
    (track.valence !== null && track.valence !== undefined);

  const { data, isPending, isError } = useTrackFeatures(track);

  if (isPending && !hasInlineDerived) {
    return (
      <div className={styles.state}>
        <div className={styles.spinner} aria-hidden="true" />
        <p className={styles.hint}>Loading features…</p>
      </div>
    );
  }

  if (isError && !hasInlineDerived) {
    return (
      <div className={styles.state}>
        <p className={styles.hint}>Could not load features. Try again after the backend is running.</p>
      </div>
    );
  }

  // data === null means a 404: the row exists but features were never computed.
  if (data === null && !hasInlineDerived) {
    return (
      <div className={styles.state}>
        <p className={styles.hint}>Features not computed for this track.</p>
        <p className={`${styles.hint} ${styles.hintDim}`}>
          Run <code>--recompute-features</code> on the backend to enable metrics.
        </p>
      </div>
    );
  }

  const get = featureGetter(track, data);

  const ePred = get('energy_pred');
  const dPred = get('danceability_pred');
  const vPred = get('valence_pred');
  const vibeML = get('vibe_score_ml');
  const modelV = get('model_version');
  const hasML = ePred !== null || dPred !== null || vPred !== null;

  const arel = get('activation_relative');
  const brightness = get('brightness') ?? get('spectral_centroid');

  const vm = get('valence_mode');
  const vmAnnot =
    typeof vm === 'number' && Number.isFinite(vm)
      ? vm > 0
        ? 'major-leaning'
        : vm < 0
          ? 'minor-leaning'
          : 'neutral'
      : '';

  const mfcc = get('mfcc_mean');
  const chroma = get('chroma_mean');

  return (
    <>
      {hasML && (
        <Group title={`ML predictions${modelV ? ` (${modelV})` : ''}`}>
          {ePred !== null && <Row label="energy_pred" value={ePred} decimals={3} />}
          {dPred !== null && <Row label="danceability_pred" value={dPred} decimals={3} />}
          {vPred !== null && <Row label="valence_pred" value={vPred} decimals={3} />}
          {vibeML !== null && (
            <Row label="vibe_score_ml" value={vibeML} decimals={3} annot="0.55E + 0.45D" />
          )}
        </Group>
      )}

      <Group title="Derived axes">
        <Row label="activation" value={get('activation')} decimals={1} />
        <Row label="valence" value={get('valence')} decimals={1} />
        <Row label="acousticness" value={get('acousticness')} decimals={2} />
        {arel !== null && <Row label="activation_rel" value={arel} decimals={1} />}
      </Group>

      <Group title="Rhythm">
        <Row label="tempo" value={get('tempo')} barMax={200} decimals={0} unit="BPM" />
        <Row label="tempo_stability" value={get('tempo_stability')} barMax={10} />
        <Row label="onset_rate" value={get('onset_rate')} barMax={10} />
      </Group>

      <Group title="Energy">
        <Row label="energy_mean" value={get('energy_mean')} barMax={1} decimals={3} annot="typical loudness" />
        <Row label="energy_std" value={get('energy_std')} barMax={1} decimals={3} annot="dynamic range" />
      </Group>

      <Group title="Timbre">
        <Row label="brightness" value={brightness} barMax={5000} decimals={0} unit="Hz" />
        <Row label="bandwidth" value={get('bandwidth')} barMax={5000} decimals={0} unit="Hz" />
        <Row label="rolloff" value={get('rolloff')} barMax={8000} decimals={0} unit="Hz" />
        <Row label="spectral_contrast" value={get('spectral_contrast')} barMax={40} />
        <Row label="flatness" value={get('flatness')} decimals={3} />
        <Row label="zcr" value={get('zcr')} decimals={3} />
        <Row label="timbre_variability" value={get('timbre_variability')} barMax={50} />
      </Group>

      <Group title="Harmony">
        <Row label="valence_mode" value={vm} barMax={1} barSigned decimals={2} annot={vmAnnot} />
        <Row label="tonnetz_std" value={get('tonnetz_std')} decimals={3} />
      </Group>

      {Array.isArray(mfcc) && mfcc.length > 0 && (
        <Group title="MFCC (13 coeffs)">
          <MiniBarChart values={mfcc} ariaLabel="MFCC coefficients" />
        </Group>
      )}

      {Array.isArray(chroma) && chroma.length > 0 && (
        <Group title="Chroma (C..B)">
          <MiniBarChart
            values={chroma}
            ariaLabel="Chroma pitch classes C through B"
            midTick
          />
          <div className={styles.chartLabels}>
            {CHROMA_LABELS.map((n) => (
              <span key={n}>{n}</span>
            ))}
          </div>
        </Group>
      )}
    </>
  );
}

export default function MetricsPanel({ open, onClose }) {
  const { current } = usePlayer();

  return (
    <Panel open={open} onClose={onClose} title="Track metrics" id="metricsPanel" wide>
      {current ? (
        // Remount on track change so the fast-path/fetch decision re-runs
        // cleanly instead of showing the previous track's bars.
        <MetricsBody track={current} key={current.spotify_id || current.id} />
      ) : (
        <div className={styles.state}>
          <p className={styles.hint}>No track loaded yet.</p>
        </div>
      )}
    </Panel>
  );
}
