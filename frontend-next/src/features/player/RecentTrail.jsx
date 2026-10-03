import { usePlayer } from '../../state/PlayerContext';
import { trackKey } from '../../lib/vibe';
import styles from './RecentTrail.module.css';

/**
 * Recently-played thumbnails. Ported from frontend/app.js:1070
 * (renderRecentTrail). The currently-playing track is excluded.
 */
export default function RecentTrail({ horizontal = false }) {
  const { recent, current, loadTrack } = usePlayer();

  const items = recent
    .filter((t) => !current || trackKey(t) !== trackKey(current))
    .slice(-8)
    .reverse();

  if (!items.length) return null;

  return (
    <div
      className={`${styles.trail} ${horizontal ? styles.trailRow : ''}`}
      aria-label="Recent tracks"
    >
      {items.map((t) => (
        <button
          key={trackKey(t)}
          className={styles.item}
          type="button"
          title={`${t.title || 'Untitled'} — ${t.artist || ''}`}
          aria-label={`Play ${t.title || 'Untitled'}`}
          onClick={() => loadTrack(t)}
        >
          {t.artwork_url && <img src={t.artwork_url} alt="" loading="lazy" />}
        </button>
      ))}
    </div>
  );
}
