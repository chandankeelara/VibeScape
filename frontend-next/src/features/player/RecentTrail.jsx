import { usePlayer } from '../../state/PlayerContext';
import { apiKey, trackKey } from '../../lib/vibe';
import { emitDj } from '../../lib/djBus';
import Icon from '../../ui/Icon';
import ui from '../../ui/controls.module.css';
import styles from './RecentTrail.module.css';

/**
 * Recently played — the last 8, newest first, current track excluded.
 *
 *   variant="tiles"     — the desktop foot row of small square tiles, with
 *                         the large Bit after them (NowPlaying)
 *   variant="shortcuts" — the phone home's two-column shortcut grid
 *
 * Going back to something from here is a choice — logged for the DJ through
 * djBus, so this component never instantiates useDj's state.
 */
export default function RecentTrail({ variant = 'tiles', className = '' }) {
  const { recent, current, loadTrack } = usePlayer();

  const pick = (t) => {
    const key = apiKey(t);
    if (key) emitDj({ track_id: key, action: 'picked', played_ratio: null, ts: Date.now() });
    loadTrack(t, { source: 'pick', trigger: 'pick', reason: 'recent' });
  };

  const items = recent
    .filter((t) => !current || trackKey(t) !== trackKey(current))
    .slice(-8)
    .reverse();

  if (!items.length) return null;

  const art = (t) => (t.artwork_url
    ? <img src={t.artwork_url} alt="" loading="lazy" />
    : <span className={styles.noArt}><Icon name="disc" size={20} /></span>);

  if (variant === 'shortcuts') {
    return (
      <section className={className} aria-labelledby="recent-h-phone">
        <div className={ui.secHead}>
          <h2 id="recent-h-phone" className={ui.secTitle}>Recently played</h2>
        </div>
        <div className={styles.shortcuts}>
          {items.map((t) => (
            <button
              key={trackKey(t)}
              type="button"
              className={styles.shortcut}
              onClick={() => pick(t)}
              aria-label={`Play ${t.title || 'Untitled'}`}
            >
              <span className={styles.sThumb}>{art(t)}</span>
              <span className={styles.sTitle}>{t.title || 'Untitled'}</span>
            </button>
          ))}
        </div>
      </section>
    );
  }

  return (
    <section className={className} aria-labelledby="recent-h">
      {/* Named for assistive tech only: the row has to fit under the hero
          at every desktop height, and the tiles explain themselves. */}
      <h2 id="recent-h" className={ui.srOnly}>Recently played</h2>
      <div className={styles.trail}>
        {items.map((t) => (
          <button
            key={trackKey(t)}
            type="button"
            className={styles.tile}
            onClick={() => pick(t)}
            aria-label={`Play ${t.title || 'Untitled'} by ${t.artist || 'unknown artist'}`}
          >
            <span className={styles.thumb}>
              {art(t)}
              <span className={styles.hoverPlay} aria-hidden="true"><Icon name="play" size={14} /></span>
            </span>
            <span className={styles.t1}>{t.title || 'Untitled'}</span>
            <span className={styles.t2}>{t.artist || ''}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
