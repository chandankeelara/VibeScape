import { usePlayer } from '../../state/PlayerContext';
import styles from './ModeToggle.module.css';

/**
 * Audio / video playback mode radio.
 *
 * ONE implementation, used by both `Transport` (normal layout) and
 * `TheaterBar` (theater layout). Two controls writing the same piece of state
 * drift — one of them gains a disabled rule or an aria fix and the other
 * doesn't — so the markup is never copied, only placed differently via
 * `className`.
 */
export default function ModeToggle({ className = '' }) {
  const { mode, setPlaybackMode, current, loadingTrack } = usePlayer();

  // While a track is being chosen there is nothing to switch to video.
  const hasVideo = !!current?.youtube_id && !loadingTrack;

  return (
    <div className={`${styles.toggle} ${className}`} role="group" aria-label="Playback mode">
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
  );
}
