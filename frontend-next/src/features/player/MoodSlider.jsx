import { usePlayer } from '../../state/PlayerContext';
import { MOOD_NAMES } from '../../lib/vibe';
import styles from './MoodSlider.module.css';

/**
 * The mood grid slider. Ported from frontend/app.js:441 (updateSliderVisual)
 * and index.html:437-468.
 *
 * The accent CSS variables are written directly by applyAccent() in
 * PlayerContext — not through React — because they change on every tick of a
 * drag and would otherwise re-render the whole tree.
 */
export default function MoodSlider() {
  const { vibe, setVibe, mood, fetchForVibe } = usePlayer();

  return (
    <section className={styles.vibe}>
      <div className={styles.hero}>
        <div className={styles.num}>{vibe}</div>
        <div className={styles.mood}>{mood}</div>
      </div>

      <div className={styles.sliderWrap}>
        <div className={styles.track}>
          <div className={styles.gradient} />
          <div className={styles.fill} style={{ width: `${vibe}%` }} />
        </div>
        <input
          className={styles.slider}
          type="range"
          min="0"
          max="100"
          step="1"
          value={vibe}
          aria-label="Vibe"
          onChange={(e) => setVibe(Number(e.target.value))}
          onMouseUp={() => fetchForVibe()}
          onTouchEnd={() => fetchForVibe()}
          onKeyUp={(e) => {
            // Commit on arrow-key release so holding an arrow doesn't fire a
            // request per keypress.
            if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) fetchForVibe();
          }}
        />
        <div className={styles.thumb} style={{ left: `${vibe}%` }} aria-hidden="true" />
      </div>

      <div className={styles.ticks}>
        {MOOD_NAMES.map((name) => (
          <span key={name} className={name === mood ? styles.tickActive : undefined}>
            {name}
          </span>
        ))}
      </div>
    </section>
  );
}
