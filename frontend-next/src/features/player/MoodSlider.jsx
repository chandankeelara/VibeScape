import { useId } from 'react';
import { usePlayer } from '../../state/PlayerContext';
import { MOODS } from '../../lib/vibe';
import Range from '../../ui/Range';
import styles from './MoodSlider.module.css';

/**
 * The vibe card — the one signature element (S2).
 *
 * A real range input (keyboard, screen reader and click-to-set for free) with
 * detent ticks at the band edges and five tappable band names. Dragging
 * re-tints the accent live: applyAccent() writes the CSS variables straight
 * onto <html> from PlayerContext, not through React, because it changes on
 * every tick of a drag.
 *
 * A new track is fetched on COMMIT (pointer up, key up, band click), never per
 * tick — holding an arrow key must not fire a request per keypress.
 *
 *   compact — the bar's popover copy: no hint line, no card chrome.
 */
const COMMIT_KEYS = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'];

export default function MoodSlider({ compact = false, className = '' }) {
  const { vibe, setVibe, mood, fetchForVibe } = usePlayer();
  const labelId = useId();

  const pickBand = (m) => {
    const centre = Math.round((m.min + m.max) / 2);
    setVibe(centre);
    fetchForVibe(centre);
  };

  return (
    <section className={`${styles.card} ${compact ? styles.compact : ''} ${className}`} aria-labelledby={labelId}>
      <div className={styles.top}>
        <div>
          <p className={styles.label} id={labelId}>Your vibe</p>
          <div className={styles.read}>
            <span className={styles.num}>{vibe}</span>
            <span className={styles.word}>{mood}</span>
          </div>
        </div>
        {!compact && <p className={styles.hint}>The DJ plays music near this energy</p>}
      </div>

      <div className={styles.slider}>
        <Range
          variant="vibe"
          min={0}
          max={100}
          step={1}
          value={vibe}
          aria-labelledby={labelId}
          aria-valuetext={`${vibe}, ${mood}`}
          onChange={(e) => setVibe(Number(e.target.value))}
          onPointerUp={() => fetchForVibe()}
          onKeyUp={(e) => { if (COMMIT_KEYS.includes(e.key)) fetchForVibe(); }}
        />
        <div className={styles.ticks} aria-hidden="true">
          {[20, 40, 60, 80].map((v) => <i key={v} style={{ left: `${v}%` }} />)}
        </div>
      </div>

      <div className={styles.bands}>
        {MOODS.map((m) => (
          <button
            key={m.name}
            type="button"
            aria-current={m.name === mood ? 'true' : undefined}
            aria-label={`Set vibe to ${m.name}`}
            onClick={() => pickBand(m)}
          >
            {m.name}
          </button>
        ))}
      </div>
    </section>
  );
}
