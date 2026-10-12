import { useEffect, useRef, useState } from 'react';
import { usePlayer } from '../../state/PlayerContext';
import { useVolume } from '../../state/useVolume';
import Icon from '../../ui/Icon';
import Range from '../../ui/Range';
import Equalizer from '../../ui/Equalizer';
import ui from '../../ui/controls.module.css';
import MoodSlider from './MoodSlider';
import { PipButton, PlayButton, Seek, Times, TransportButtons } from './Transport';
import styles from './PlayerBar.module.css';

/**
 * The persistent player bar along the bottom (desktop), which IS the
 * mini-player on a phone — the same element restyled, the same buttons.
 *
 * Progress sits ABOVE the transport (YouTube Music): a full-width line on the
 * bar's top edge, taller hit area than line. Elapsed and total flank the
 * transport so play stays dead centre.
 */

function VibeChip() {
  const { vibe, mood } = usePlayer();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!wrapRef.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className={styles.chipWrap} ref={wrapRef}>
      <button
        type="button"
        className={`${ui.pill} ${styles.vibeChip}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={`Vibe ${vibe}, ${mood}. Change vibe`}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={styles.chipDot} aria-hidden="true" />
        <span className={styles.chipNum}>{vibe}</span>
        <span className={styles.chipWord}>{mood}</span>
      </button>
      {open && (
        <div className={styles.vibePop} role="dialog" aria-label="Vibe">
          <MoodSlider compact />
        </div>
      )}
    </div>
  );
}

function Volume() {
  const { volume, muted, setVolume, toggleMute } = useVolume();
  const pct = Math.round((muted ? 0 : volume) * 100);
  const icon = muted || pct === 0 ? 'mute' : pct < 50 ? 'volLow' : 'vol';
  return (
    <div className={styles.vol}>
      <button type="button" className={ui.ib} onClick={toggleMute} aria-label={muted ? 'Unmute' : 'Mute'} aria-pressed={muted}>
        <Icon name={icon} />
      </button>
      <Range
        variant="volume"
        min={0}
        max={100}
        step={1}
        value={pct}
        aria-label="Volume"
        aria-valuetext={`${pct}%`}
        onChange={(e) => setVolume(Number(e.target.value) / 100)}
      />
    </div>
  );
}

export default function PlayerBar({
  queueOpen, onToggleQueue, onOpenNowPlaying,
  pipSupported = false, pipOpen = false, pipAuto = false, onTogglePip,
}) {
  const { current, loadingTrack, next } = usePlayer();

  return (
    <footer className={styles.bar} aria-label="Player">
      <div className={styles.seek}>
        <Seek variant="seekBar" className={styles.seekDesk} />
        <Seek variant="hairline" className={styles.seekPhone} />
      </div>

      <div className={styles.left}>
        <button
          type="button"
          className={styles.now}
          onClick={onOpenNowPlaying}
          aria-label={current ? `Open now playing: ${current.title}` : 'Open now playing'}
        >
          <span className={styles.thumb}>
            {current?.artwork_url ? <img src={current.artwork_url} alt="" /> : <Icon name="disc" size={22} />}
          </span>
          {loadingTrack ? (
            <span className={styles.pick}>
              <span className={ui.dots} aria-hidden="true"><i /><i /><i /></span>Picking the next track…
            </span>
          ) : (
            <span className={styles.text}>
              <span className={styles.title}>
                <span className={styles.titleText}>{current?.title || 'Nothing playing'}</span>
                {current && <Equalizer />}
              </span>
              <span className={styles.artist}>{current?.artist || 'Move the vibe to begin'}</span>
            </span>
          )}
        </button>
      </div>

      <div className={styles.center}>
        <Times which="elapsed" className={styles.timeDesk} />
        <span className={styles.desk}><TransportButtons /></span>
        <span className={styles.phone}>
          <PlayButton size="bare" />
          <button type="button" className={ui.ib} onClick={() => next()} aria-label="Next">
            <Icon name="next" size={22} />
          </button>
        </span>
        <Times which="total" className={styles.timeDesk} />
      </div>

      <div className={styles.right}>
        <VibeChip />
        <span className={styles.divider} aria-hidden="true" />
        {pipSupported && <PipButton open={pipOpen} auto={pipAuto} onToggle={onTogglePip} />}
        <button
          type="button"
          className={`${ui.ib} ${ui.dot}`}
          aria-pressed={queueOpen}
          aria-label={queueOpen ? 'Hide queue' : 'Show queue'}
          onClick={onToggleQueue}
        >
          <Icon name="queue" />
        </button>
        <Volume />
      </div>
    </footer>
  );
}
