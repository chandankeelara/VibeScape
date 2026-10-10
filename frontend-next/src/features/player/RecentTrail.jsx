import { usePlayer } from '../../state/PlayerContext';
import { apiKey, trackKey } from '../../lib/vibe';
import { emitDj } from '../../lib/djBus';
import styles from './RecentTrail.module.css';

/**
 * Recently-played thumbnails. Ported from frontend/app.js:1070
 * (renderRecentTrail). The currently-playing track is excluded.
 */
export default function RecentTrail() {
  const { recent, current, loadTrack } = usePlayer();

  // Going back to something from the trail is a choice — log it for the DJ
  // (djBus, so this component never instantiates useDj's state).
  const pick = (t) => {
    const key = apiKey(t);
    if (key) emitDj({ track_id: key, action: 'picked', played_ratio: null, ts: Date.now() });
    loadTrack(t, { source: 'pick', trigger: 'pick' });
  };

  const items = recent
    .filter((t) => !current || trackKey(t) !== trackKey(current))
    .slice(-8)
    .reverse();

  if (!items.length) return null;

  return (
    <div className={styles.trail} aria-label="Recent tracks">
      {items.map((t) => (
        <button
          key={trackKey(t)}
          className={styles.item}
          type="button"
          title={`${t.title || 'Untitled'} — ${t.artist || ''}`}
          aria-label={`Play ${t.title || 'Untitled'}`}
          onClick={() => pick(t)}
        >
          {t.artwork_url && <img src={t.artwork_url} alt="" loading="lazy" />}
        </button>
      ))}
    </div>
  );
}
