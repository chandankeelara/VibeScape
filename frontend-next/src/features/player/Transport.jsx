import { useCallback, useRef } from 'react';
import { usePlayer, usePlaybackTime } from '../../state/PlayerContext';
import { fmtTime } from '../../lib/vibe';
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
  const { playing, togglePlay, next, prev, mode, setPlaybackMode, current, loadingTrack } =
    usePlayer();

  // While a track is being chosen there is nothing to play, pause, go back
  // from, or switch to video — those controls act on a track that is on its
  // way out. `next` stays live on purpose: pressing it again during a slow
  // pick should skip onward, not be swallowed.
  const hasVideo = !!current?.youtube_id && !loadingTrack;

  return (
    <section className={styles.transport}>
      <ProgressBar />

      <div className={styles.controls}>
        <button className={styles.ghost} type="button" onClick={prev} aria-label="Previous" disabled={loadingTrack}>
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="19 20 9 12 19 4 19 20" /><line x1="5" y1="19" x2="5" y2="5" />
          </svg>
        </button>

        <button
          className={styles.play}
          type="button"
          onClick={togglePlay}
          aria-label={playing ? 'Pause' : 'Play'}
          disabled={loadingTrack}
        >
          {playing ? (
            <svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor">
              <rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor">
              <path d="M8 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 8 5.5z" />
            </svg>
          )}
        </button>

        <button className={styles.ghost} type="button" onClick={next} aria-label="Next">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="5 4 15 12 5 20 5 4" /><line x1="19" y1="5" x2="19" y2="19" />
          </svg>
        </button>

        <div className={styles.modeToggle} role="group" aria-label="Playback mode">
          <button
            type="button" role="radio" aria-checked={mode === 'audio'} title="Audio playback"
            disabled={loadingTrack}
            className={mode === 'audio' ? styles.modeActive : styles.mode}
            onClick={() => setPlaybackMode('audio')}
          >
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" />
            </svg>
            <span>audio</span>
          </button>
          <button
            type="button" role="radio" aria-checked={mode === 'video'} disabled={!hasVideo}
            title={hasVideo ? 'Video playback' : 'No video for this track'}
            className={mode === 'video' ? styles.modeActive : styles.mode}
            onClick={() => setPlaybackMode('video')}
          >
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="2" y="6" width="20" height="12" rx="2" /><polygon points="10 9 15 12 10 15 10 9" />
            </svg>
            <span>video</span>
          </button>
        </div>
      </div>
    </section>
  );
}
