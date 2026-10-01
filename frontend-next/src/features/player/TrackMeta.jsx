import { usePlayer } from '../../state/PlayerContext';
import { trackVibe, moodFor, isMetadataOnly, classificationLabel } from '../../lib/vibe';
import Skeleton from './Skeleton';
import styles from './TrackMeta.module.css';

/**
 * Title / artist / album / genre + the chip row.
 * Ported from frontend/app.js:1185-1250 and index.html:363-394.
 */
export default function TrackMeta({ onShowMetrics }) {
  const { current, source, loadingTrack, verifying, startVerify, stopVerify, canVerify } = usePlayer();

  // Checked BEFORE `current`, because during a track change `current` is still
  // the track that just finished — so without this the hero card shows the
  // loading robot while the title under it names the previous song. The bar
  // widths deliberately mismatch so the block reads as text, not as a form.
  if (loadingTrack) {
    return (
      <section className={styles.meta} aria-busy="true">
        <div className={styles.titleRow}>
          <Skeleton w="64%" h="clamp(26px, 3.6dvh, 42px)" />
        </div>
        <div className={styles.sub}>
          <Skeleton w="38%" h="clamp(13px, 1.8dvh, 16px)" />
        </div>
        <div className={styles.chips}>
          <Skeleton w="62px" h="22px" r="var(--radius-pill)" />
          <Skeleton w="52px" h="22px" r="var(--radius-pill)" />
          <Skeleton w="72px" h="22px" r="var(--radius-pill)" />
        </div>
      </section>
    );
  }

  if (!current) {
    return (
      <section className={styles.meta}>
        {/* Reached only when idle: loadingTrack is handled above. */}
        <h1 className={styles.title}>Move the slider to begin</h1>
        <div className={styles.sub}><span className={styles.artist}>—</span></div>
      </section>
    );
  }

  const vibe = trackVibe(current);
  const mood = vibe != null ? moodFor(vibe).name : null;

  return (
    <section className={styles.meta}>
      <div className={styles.titleRow}>
        <h1 className={styles.title}>{current.title || 'Untitled'}</h1>
        {vibe != null && <span className={styles.titleVibe}>vibe {vibe}</span>}
        {current.language && <span className={styles.titleLang}>{current.language}</span>}
        {isMetadataOnly(current) && (
          <span className={styles.sdkOnly} title="No local audio — playable only via Spotify Premium">
            sdk-only
          </span>
        )}
      </div>

      <div className={styles.sub}>
        <span className={styles.artist}>{current.artist || 'Unknown artist'}</span>
        {current.album && <><span className={styles.sep}>·</span><span className={styles.album}>{current.album}</span></>}
      </div>

      {current.genre && <p className={styles.genreLine}>in <em>{current.genre}</em></p>}

      <div className={styles.chips}>
        {mood && <span className={`${styles.chip} ${styles.chipMood}`}>{mood}</span>}
        {vibe != null && <span className={`${styles.chip} ${styles.chipVibe}`}>vibe {vibe}</span>}
        {source && (
          <span className={`${styles.chip} ${styles.chipSource}`} data-source={source}>
            {source === 'spotify' ? 'SPOTIFY' : 'PREVIEW'}
          </span>
        )}
        {/* Plays the exact 30s clip the model was given for this track. */}
        <button
          className={`${styles.chip} ${styles.chipBtn} ${verifying ? styles.chipVerifying : ''}`}
          type="button"
          onClick={verifying ? stopVerify : startVerify}
          disabled={!canVerify && !verifying}
          title={
            canVerify
              ? `Play the 30-sec clip used to classify this track — ${classificationLabel(current.classification_source)}`
              : 'No classification audio available for this track'
          }
        >
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M3 12a9 9 0 0 1 18 0v5a2 2 0 0 1-2 2h-2v-7h4" />
            <path d="M3 12v5a2 2 0 0 0 2 2h2v-7H3" />
          </svg>
          <span>{verifying ? 'stop' : 'verify'}</span>
        </button>

        {onShowMetrics && (
          <button
            className={`${styles.chip} ${styles.chipBtn}`}
            type="button"
            onClick={onShowMetrics}
            title="Track metrics (Ctrl+Shift+M)"
          >
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 3v18h18" /><path d="M7 15l4-6 4 3 5-7" />
            </svg>
            <span>metrics</span>
          </button>
        )}
      </div>
    </section>
  );
}
