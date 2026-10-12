import { useState } from 'react';
import { usePlayer, usePlaybackTime } from '../../state/PlayerContext';
import { fmtTime } from '../../lib/vibe';
import Icon from '../../ui/Icon';
import Range from '../../ui/Range';
import ui from '../../ui/controls.module.css';
import styles from './Transport.module.css';

/**
 * Transport building blocks shared by the player bar (desktop), the
 * mini-player and the phone now-playing screen.
 *
 * Seek and Times subscribe to usePlaybackTime() themselves: it updates ~4x a
 * second, and keeping that subscription in leaves stops the rest of the
 * player tree re-rendering while a track plays.
 */

/**
 * The seek line. A real range input; dragging previews locally and SEEKS ON
 * RELEASE (pointer up / key up), so a drag is one seek, not hundreds.
 *
 * While the next track is being chosen the time feed still belongs to the
 * track that just finished — usually parked at 100% — so the bar is emptied
 * and disabled until the real numbers arrive.
 */
export function Seek({ variant = 'seekBar', className = '' }) {
  const { seek, loadingTrack } = usePlayer();
  const { position, duration } = usePlaybackTime();
  const [drag, setDrag] = useState(null);

  const live = loadingTrack || duration <= 0 ? 0 : Math.min(1000, Math.round((position / duration) * 1000));
  const value = drag ?? live;
  const commit = () => {
    if (drag == null) return;
    seek(drag / 1000);
    setDrag(null);
  };
  const shown = loadingTrack ? 0 : (drag != null ? (drag / 1000) * duration : position);

  return (
    <Range
      variant={variant}
      className={className}
      min={0}
      max={1000}
      step={1}
      value={value}
      disabled={loadingTrack || duration <= 0}
      aria-label="Seek"
      aria-valuetext={loadingTrack ? 'Loading' : `${fmtTime(shown)} of ${fmtTime(duration)}`}
      tabIndex={variant === 'hairline' ? -1 : undefined}
      aria-hidden={variant === 'hairline' ? 'true' : undefined}
      onChange={(e) => setDrag(Number(e.target.value))}
      onPointerUp={commit}
      onKeyUp={commit}
      onBlur={commit}
    />
  );
}

/** Elapsed / total labels. `which` = 'elapsed' | 'total' | 'both'. */
export function Times({ which = 'both', className = '' }) {
  const { loadingTrack } = usePlayer();
  const { position, duration } = usePlaybackTime();
  const el = loadingTrack ? '--:--' : fmtTime(position);
  const tot = loadingTrack ? '--:--' : fmtTime(duration);
  if (which === 'elapsed') return <span className={`${styles.time} ${styles.timeEl} ${className}`}>{el}</span>;
  if (which === 'total') return <span className={`${styles.time} ${className}`}>{tot}</span>;
  return (
    <div className={`${styles.times} ${className}`}>
      <span>{el}</span><span>{tot}</span>
    </div>
  );
}

/** Play/pause with a glyph crossfade, so the change reads as a change. */
export function PlayButton({ size = 'md', className = '' }) {
  const { playing, togglePlay, loadingTrack } = usePlayer();
  const on = playing && !loadingTrack;
  const glyph = size === 'lg' ? 28 : size === 'bare' ? 24 : 20;
  return (
    <button
      type="button"
      className={`${ui.play} ${styles[`play_${size}`] || ''} ${className}`}
      onClick={togglePlay}
      aria-label={on ? 'Pause' : 'Play'}
      disabled={loadingTrack}
    >
      <span className={`${ui.glyph} ${on ? ui.glyphOff : ''}`}><Icon name="play" size={glyph} /></span>
      <span className={`${ui.glyph} ${on ? '' : ui.glyphOff}`}><Icon name="pause" size={glyph} /></span>
    </button>
  );
}

/**
 * prev / play / next. `prev` and `play` are disabled while the next track is
 * being chosen — they act on a track on its way out. `next` stays live: a
 * second press during a slow pick should skip onward, not be swallowed.
 */
export function TransportButtons({ size = 'md', showPrev = true }) {
  const { next, prev, loadingTrack } = usePlayer();
  const icon = size === 'lg' ? 28 : 22;
  return (
    <>
      {showPrev && (
        <button type="button" className={`${ui.ib} ${styles[`skip_${size}`] || ''}`} onClick={() => prev()} aria-label="Previous" disabled={loadingTrack}>
          <Icon name="prev" size={icon} />
        </button>
      )}
      <PlayButton size={size} />
      <button type="button" className={`${ui.ib} ${styles[`skip_${size}`] || ''}`} onClick={() => next()} aria-label="Next">
        <Icon name="next" size={icon} />
      </button>
    </>
  );
}

/**
 * Audio | Video — the YouTube Music "Song / Video" switch. One
 * implementation everywhere; placed differently via className.
 */
export function ModeSwitch({ className = '' }) {
  const { mode, setPlaybackMode, current, loadingTrack } = usePlayer();
  const hasVideo = !!current?.youtube_id && !loadingTrack;
  return (
    <div className={`${ui.seg} ${className}`} role="radiogroup" aria-label="Playback source">
      <button
        type="button"
        role="radio"
        aria-checked={mode === 'audio'}
        className={ui.segBtn}
        disabled={loadingTrack}
        onClick={() => setPlaybackMode('audio')}
      >
        <Icon name="music" size={16} />Audio
      </button>
      <button
        type="button"
        role="radio"
        aria-checked={mode === 'video'}
        className={ui.segBtn}
        disabled={!hasVideo && mode !== 'video'}
        title={hasVideo ? 'Watch the video' : 'No video for this track'}
        onClick={() => setPlaybackMode('video')}
      >
        <Icon name="video" size={16} />Video
      </button>
    </div>
  );
}

/** Theater toggle — rendered only in video mode, by the caller. */
export function TheaterButton({ theater, onToggle, className = '' }) {
  return (
    <button type="button" className={`${ui.pill} ${className}`} aria-pressed={theater} onClick={onToggle}>
      <Icon name={theater ? 'normal' : 'theater'} size={16} />
      <span>{theater ? 'Exit theater' : 'Theater'}</span>
    </button>
  );
}

/** Picture-in-picture. Not rendered at all where Document PiP is unavailable. */
export function PipButton({ open, auto = false, onToggle, className = '' }) {
  const title = open
    ? 'Close miniplayer'
    : auto
      ? 'Miniplayer — opens by itself when you switch tabs'
      : 'Miniplayer — opens now, and when you switch tabs';
  return (
    <button
      type="button"
      className={`${ui.ib} ${className}`}
      onClick={onToggle}
      aria-pressed={open}
      aria-label={open ? 'Close miniplayer' : 'Open miniplayer'}
      title={title}
    >
      <Icon name="pip" />
    </button>
  );
}
