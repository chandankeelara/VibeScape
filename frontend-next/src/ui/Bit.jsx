import { Component, useEffect, useRef, useState } from 'react';
import { onMascot } from '../lib/mascotBus';
import styles from './Bit.module.css';

/**
 * Bit, S2 size — small, flat, reacts once and returns to rest. Sits beside
 * the DJ line and speaks for it.
 *
 * Replaces the full-size BitDock rig on the player page (the rig, its 2,000
 * lines of animation and BitLab remain on disk for the ?bit review page).
 *
 * Mood comes from the mascot bus (lib/mascotBus.js): a finished track makes
 * him happy, a skip makes him sad, for 2.6s. `thinking` is driven by the
 * caller (the DJ is picking) and holds for as long as it is true.
 */

const REACTION = { completed: 'happy', skipped: 'sad' };
const HOLD_MS = 2600;

function BitSvg({ mood, className }) {
  const cls = [styles.bit, mood ? styles[mood] : '', className].filter(Boolean).join(' ');
  return (
    <svg className={cls} viewBox="16 -1 68 88" aria-hidden="true" focusable="false">
      <g className={styles.body}>
        <rect className={styles.part} x="44" y="43" width="12" height="12" rx="5" />
        <rect className={styles.part} x="31" y="51" width="38" height="33" rx="12" />
        <circle className={styles.mark} cx="50" cy="65" r="4.6" />
        <g className={styles.head}>
          <path className={styles.line} d="M26 29 Q50 3 74 29" />
          <rect className={styles.part} x="21" y="22" width="11" height="17" rx="5.5" />
          <rect className={styles.part} x="68" y="22" width="11" height="17" rx="5.5" />
          <line className={styles.line} x1="50" y1="12" x2="50" y2="6" />
          <circle className={`${styles.mark} ${styles.bulb}`} cx="50" cy="3.6" r="3.2" />
          <rect className={styles.part} x="28" y="12" width="44" height="34" rx="12" />
          <rect className={styles.visor} x="34" y="20" width="32" height="17" rx="8" />
          <g className={`${styles.eyes} ${styles.eOpen}`}>
            <circle cx="42" cy="28.5" r="3.4" /><circle cx="58" cy="28.5" r="3.4" />
          </g>
          <g className={`${styles.eyes} ${styles.eHappy}`}>
            <path d="M38.5 30 Q42 24.5 45.5 30" /><path d="M54.5 30 Q58 24.5 61.5 30" />
          </g>
          <g className={`${styles.eyes} ${styles.eSad}`}>
            <path d="M38.5 27 L45.5 29" /><path d="M54.5 29 L61.5 27" />
            <circle cx="42" cy="31.6" r="2.2" /><circle cx="58" cy="31.6" r="2.2" />
          </g>
        </g>
      </g>
    </svg>
  );
}

function BitInner({ thinking = false, reactive = true, className }) {
  const [reaction, setReaction] = useState(null);
  const timer = useRef(0);

  useEffect(() => {
    if (!reactive) return undefined;
    const off = onMascot((action) => {
      const mood = REACTION[action];
      if (!mood) return;
      clearTimeout(timer.current);
      // Drop to rest for a frame so a second skip in a row restarts the pose.
      setReaction(null);
      requestAnimationFrame(() => setReaction(mood));
      timer.current = setTimeout(() => setReaction(null), HOLD_MS);
    });
    return () => { off(); clearTimeout(timer.current); };
  }, [reactive]);

  return <BitSvg mood={thinking ? 'thinking' : reaction} className={className} />;
}

/** Decoration must never take the player down with it. */
class BitBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { dead: false };
  }

  static getDerivedStateFromError() {
    return { dead: true };
  }

  render() {
    return this.state.dead ? null : this.props.children;
  }
}

export default function Bit(props) {
  return (
    <BitBoundary>
      <BitInner {...props} />
    </BitBoundary>
  );
}
