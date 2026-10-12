import { useEffect, useRef, useState } from 'react';
import { usePlayer } from '../../state/PlayerContext';
import { useTheme } from '../../state/ThemeContext';
import { artColor } from '../../lib/artColor';
import { tintFor } from '../../lib/vibe';
import { useQueueState } from '../queue';
import Icon from '../../ui/Icon';
import Equalizer from '../../ui/Equalizer';
import ui from '../../ui/controls.module.css';
import ArtStage from './ArtStage';
import TrackMeta from './TrackMeta';
import MoodSlider from './MoodSlider';
import DjLine from './DjLine';
import RecentTrail from './RecentTrail';
import { ModeSwitch, Seek, Times, TheaterButton, TransportButtons } from './Transport';
import styles from './NowPlaying.module.css';

/**
 * The now-playing panel (desktop) — which IS the phone's full-screen
 * now-playing view, restyled. One element in every layout, so the stage and
 * the YouTube mount inside it never move in the DOM (frontend-next/CLAUDE.md,
 * "The YouTube mount").
 *
 * Theater is a class here: the stage takes the whole column and the meta
 * folds into two columns underneath. The queue panel stays where it is.
 */

/** The art's colour, as a static room tint plus the stage's light pool. */
function useArtColour(url) {
  const [colour, setColour] = useState(null);
  useEffect(() => {
    let live = true;
    setColour(null);
    artColor(url).then((c) => { if (live) setColour(c); });
    return () => { live = false; };
  }, [url]);
  return colour;
}

/** Two-layer crossfade of the static tint on track change. */
function Tint({ background }) {
  const [layers, setLayers] = useState([]);
  const seq = useRef(0);
  useEffect(() => {
    if (!background) { setLayers([]); return; }
    const id = ++seq.current;
    setLayers((prev) => [...prev.slice(-1), { background, id }]);
  }, [background]);
  useEffect(() => {
    if (layers.length < 2) return undefined;
    const t = setTimeout(() => setLayers((p) => p.slice(-1)), 650);
    return () => clearTimeout(t);
  }, [layers]);
  return (
    <div className={styles.tint} aria-hidden="true">
      {layers.map((l, i) => (
        <div
          key={l.id}
          className={i === layers.length - 1 && layers.length > 1 ? styles.tintIn : ''}
          style={{ background: l.background }}
        />
      ))}
    </div>
  );
}

export default function NowPlaying({
  theater = false, canTheater = false, onToggleTheater,
  phoneOpen = false, onPhoneClose, onOpenQueue, onShowMetrics,
}) {
  const { current, mode } = usePlayer();
  const { djEnabled } = useQueueState();
  const { theme } = useTheme();
  const colour = useArtColour(current?.artwork_url || '');
  const isVideo = mode === 'video';

  const tint = colour
    ? `radial-gradient(90% 70% at 12% 0%, ${tintFor(colour.hue, theme)}, transparent 75%)`
    : null;
  const pool = colour ? `rgb(${colour.rgb.join(' ')})` : undefined;

  const cls = [
    styles.np,
    isVideo ? styles.isVideo : '',
    theater ? styles.isTheater : '',
    phoneOpen ? styles.phoneOpen : '',
  ].filter(Boolean).join(' ');

  return (
    <main className={cls} aria-label="Now playing">
      <Tint background={tint} />

      <div className={styles.head}>
        <button type="button" className={`${ui.ib} ${styles.phoneOnly}`} onClick={onPhoneClose} aria-label="Minimise player">
          <Icon name="down" />
        </button>
        <div className={styles.headLeft}>
          <ModeSwitch className={styles.seg} />
          {isVideo && canTheater && (
            <TheaterButton theater={theater} onToggle={onToggleTheater} className={styles.deskOnly} />
          )}
        </div>
        <p className={`${styles.from} ${styles.deskOnly}`}>
          <Equalizer />
          <span>Playing from <b>{djEnabled ? 'DJ mode' : 'your vibe'}</b></span>
        </p>
        <button type="button" className={`${ui.ib} ${styles.phoneOnly}`} onClick={onOpenQueue} aria-label="Open queue">
          <Icon name="queue" />
        </button>
      </div>

      <div className={styles.scroll}>
        <div className={styles.hero}>
          <ArtStage className={styles.stage} pool={pool} />

          <div className={styles.meta}>
            <div className={styles.info}>
              <TrackMeta onShowMetrics={onShowMetrics} compact={theater} />
            </div>

            {/* Phone only: the transport lives inside the now-playing screen. */}
            <div className={styles.phTransport}>
              <Seek variant="seekPhone" />
              <Times />
              <div className={styles.phButtons}>
                <TransportButtons size="lg" />
              </div>
            </div>

            <div className={styles.side}>
              <MoodSlider />
              <DjLine />
            </div>
          </div>
        </div>

        <RecentTrail variant="tiles" className={styles.recent} />
      </div>
    </main>
  );
}
