import { usePlayer } from '../../state/PlayerContext';
import { trackVibe, moodFor, isMetadataOnly } from '../../lib/vibe';
import styles from './TrackMeta.module.css';

/**
 * Title / artist / album / genre + the chip row.
 * Ported from frontend/app.js:1185-1250 and index.html:363-394.
 */
export default function TrackMeta({ onShowMetrics }) {
  const { current, source, loadingTrack } = usePlayer();

  if (!current) {
    return (
      <section className={styles.meta}>
        <h1 className={styles.title}>
          {loadingTrack ? 'Finding a track…' : 'Move the slider to begin'}
        </h1>
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
