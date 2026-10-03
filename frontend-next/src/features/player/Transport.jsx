import { useCallback, useRef } from 'react';
import { usePlayer, usePlaybackTime } from '../../state/PlayerContext';
import { fmtTime } from '../../lib/vibe';
import TransportButtons from './TransportButtons';
import ModeToggle from './ModeToggle';
import styles from './Transport.module.css';

/**
 * Progress bar + transport controls + audio/video mode toggle.
 * Ported from frontend/app.js:2602-2730 and index.html:395-435.
 *
 * ProgressBar is deliberately its own component: usePlaybackTime() updates
 * ~4x/second, and keeping that subscription in a leaf stops the rest of the
 * player tree from re-rendering while a track plays.
 */

function ProgressBar() {
  const { seek, loadingTrack } = usePlayer();
  const { position, duration } = usePlaybackTime();
  const trackRef = useRef(null);

  // While the next track is being chosen the time feed still belongs to the
  // track that just finished — usually parked at 100%. Showing that under a
  // "finding your next track" card reads as a stuck player, so the bar is
  // emptied and the clock blanked until the real numbers arrive.
  const pct = loadingTrack ? 0 : (duration > 0 ? Math.min(100, (position / duration) * 100) : 0);

  const fracFromEvent = useCallback((clientX) => {
    const rect = trackRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return 0;
    return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  }, []);

  const onKeyDown = (e) => {
    if (loadingTrack || duration <= 0) return;
    const step = 5 / duration;
    if (e.key === 'ArrowRight') { e.preventDefault(); seek(position / duration + step); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); seek(position / duration - step); }
    else if (e.key === 'Home') { e.preventDefault(); seek(0); }
    else if (e.key === 'End') { e.preventDefault(); seek(1); }
  };

  return (
    <>
      <div
        ref={trackRef}
        className={styles.progress}
        role="slider"
        tabIndex={loadingTrack ? -1 : 0}
        aria-label="Seek"
        aria-disabled={loadingTrack || undefined}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        aria-valuetext={loadingTrack ? 'Loading' : `${fmtTime(position)} of ${fmtTime(duration)}`}
        onKeyDown={onKeyDown}
        onClick={loadingTrack ? undefined : (e) => seek(fracFromEvent(e.clientX))}
      >
        <div className={styles.progressTrack}>
          <div className={styles.progressFill} style={{ width: `${pct}%` }} />
          <div className={styles.progressThumb} style={{ left: `${pct}%` }} />
        </div>
      </div>
      <div className={styles.times}>
        <span>{loadingTrack ? '--:--' : fmtTime(position)}</span>
        <span>{loadingTrack ? '--:--' : fmtTime(duration)}</span>
      </div>
    </>
  );
}

export default function Transport() {
  return (
    <section className={styles.transport}>
      <ProgressBar />

      {/* The three buttons and the mode pill are shared with TheaterBar —
          see TransportButtons.jsx / ModeToggle.jsx. TransportButtons renders
          a fragment, so .controls is still their direct flex parent and still
          owns the gap between them, exactly as when they were inline here. */}
      <div className={styles.controls}>
        <TransportButtons />
        <ModeToggle className={styles.modeAnchor} />
      </div>
    </section>
  );
}
