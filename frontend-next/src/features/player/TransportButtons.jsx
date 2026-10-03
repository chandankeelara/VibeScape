import { usePlayer } from '../../state/PlayerContext';
import styles from './TransportButtons.module.css';

/**
 * prev / play-pause / next — one implementation, used by both `Transport`
 * (normal layout) and `TheaterBar`.
 *
 * Returns a FRAGMENT on purpose: the caller's flex row stays the direct
 * parent, so `.controls` keeps owning the spacing exactly as it did when
 * these three buttons were inline in Transport.jsx.
 *
 * `prev` and `play` are disabled while the next track is being chosen — they
 * act on a track that is on its way out. `next` stays live deliberately:
 * pressing it again during a slow pick should skip onward, not be swallowed.
 */
export default function TransportButtons({ compact = false }) {
  const { playing, togglePlay, next, prev, loadingTrack } = usePlayer();

  const ghost = compact ? styles.ghostCompact : styles.ghost;
  const play = compact ? styles.playCompact : styles.play;
  const sm = compact ? 18 : 20;
  const lg = compact ? 22 : 26;

  return (
    <>
      <button className={ghost} type="button" onClick={prev} aria-label="Previous" disabled={loadingTrack}>
        <svg viewBox="0 0 24 24" width={sm} height={sm} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polygon points="19 20 9 12 19 4 19 20" /><line x1="5" y1="19" x2="5" y2="5" />
        </svg>
      </button>

      <button
        className={play}
        type="button"
        onClick={togglePlay}
        aria-label={playing ? 'Pause' : 'Play'}
        disabled={loadingTrack}
      >
        {playing ? (
          <svg viewBox="0 0 24 24" width={lg} height={lg} fill="currentColor">
            <rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" width={lg} height={lg} fill="currentColor">
            <path d="M8 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 8 5.5z" />
          </svg>
        )}
      </button>

      <button className={ghost} type="button" onClick={next} aria-label="Next">
        <svg viewBox="0 0 24 24" width={sm} height={sm} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polygon points="5 4 15 12 5 20 5 4" /><line x1="19" y1="5" x2="19" y2="19" />
        </svg>
      </button>
    </>
  );
}
