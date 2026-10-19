import { useEffect, useRef } from 'react';
import ui from '../../ui/controls.module.css';
import { useLyrics } from './LyricsContext';
import { FINE_STEP_MS, STEP_MS, formatOffset, speakOffset } from './lyricsMath';
import styles from './SyncBar.module.css';

/**
 * [−0.5s]  +1.5s  [+0.5s]  [Tap to sync]  [Reset]
 *
 * A click on ± steps 0.5s; holding it steps 0.1s and repeats. From the
 * keyboard, Shift+Enter steps 0.1s. The offset is remembered per recording
 * (see lyricsStore.js).
 */

const HOLD_DELAY_MS = 400;
const HOLD_REPEAT_MS = 140;

export default function SyncBar({ tone = 'themed' }) {
  const { offset, nudge, reset, tapSync } = useLyrics();

  return (
    <div className={`${styles.bar} ${styles[tone]}`} role="group" aria-label="Lyrics timing">
      <StepButton sign={-1} onStep={nudge} />
      <output className={styles.value} aria-live="polite">
        <span className={ui.srOnly}>{speakOffset(offset)}</span>
        <span aria-hidden="true">{formatOffset(offset)}</span>
      </output>
      <StepButton sign={1} onStep={nudge} />
      <button type="button" className={styles.btn} onClick={tapSync} title="Press as the highlighted line starts">
        Tap to sync
      </button>
      <button type="button" className={styles.btn} onClick={reset} disabled={!offset}>
        Reset
      </button>
    </div>
  );
}

/** ± with press-and-hold fine stepping. */
function StepButton({ sign, onStep }) {
  const holdTimer = useRef(0);
  const repeatTimer = useRef(0);
  const held = useRef(false);

  const stop = () => {
    clearTimeout(holdTimer.current);
    clearInterval(repeatTimer.current);
  };
  useEffect(() => stop, []);

  const onPointerDown = (e) => {
    if (e.button) return; // primary button / touch / pen only
    held.current = false;
    stop();
    holdTimer.current = setTimeout(() => {
      held.current = true;
      onStep(sign * FINE_STEP_MS);
      repeatTimer.current = setInterval(() => onStep(sign * FINE_STEP_MS), HOLD_REPEAT_MS);
    }, HOLD_DELAY_MS);
  };

  const onClick = (e) => {
    // The hold already stepped; the click that ends it must not add 0.5s.
    if (held.current) { held.current = false; return; }
    onStep(sign * (e.shiftKey ? FINE_STEP_MS : STEP_MS));
  };

  const word = sign < 0 ? 'earlier' : 'later';
  return (
    <button
      type="button"
      className={styles.btn}
      aria-label={`Lyrics ${word} by half a second`}
      title={`Lyrics ${word} by 0.5s. Hold, or Shift+click, for 0.1s`}
      onPointerDown={onPointerDown}
      onPointerUp={stop}
      onPointerLeave={stop}
      onPointerCancel={stop}
      onContextMenu={(e) => e.preventDefault()}
      onClick={onClick}
    >
      {sign < 0 ? '−0.5s' : '+0.5s'}
    </button>
  );
}
