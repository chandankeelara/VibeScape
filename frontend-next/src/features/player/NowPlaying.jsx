import { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { usePlayer } from '../../state/PlayerContext';
import { useTheme } from '../../state/ThemeContext';
import { artColor } from '../../lib/artColor';
import { tintFor } from '../../lib/vibe';
import { useQueueState } from '../queue';
import { useLyrics } from '../lyrics';
import useMediaQuery, { BELOW_THEATER, PHONE } from '../../lib/useMediaQuery';
import Icon from '../../ui/Icon';
import Equalizer from '../../ui/Equalizer';
import ui from '../../ui/controls.module.css';
import ArtStage from './ArtStage';
import TrackMeta from './TrackMeta';
import MoodSlider from './MoodSlider';
import DjLine from './DjLine';
import RecentTrail from './RecentTrail';
import { LyricsButton, ModeSwitch, Seek, Times, TheaterButton, TransportButtons } from './Transport';
import styles from './NowPlaying.module.css';

/**
 * The now-playing panel (desktop) — which IS the phone's full-screen
 * now-playing view, restyled. One element in every layout, so the stage and
 * the YouTube mount inside it never move in the DOM (frontend-next/CLAUDE.md,
 * "The YouTube mount").
 *
 * Theater is a class here: the stage takes the whole column and the meta
 * folds into two columns underneath. The queue panel stays where it is.
 *
 * Lyrics, when on, go where each view has room without moving the stage:
 *   audio            over the artwork (ArtStage's overlay)
 *   video            in the side column, in place of the vibe controls
 *   theater          subtitles on the video, the sync bar under it
 *   below 1024px     a full-screen sheet (on a phone, only while this view is
 *                    open — the sheet belongs to it)
 */

// Loaded on first use — see features/lyrics/index.js.
const LyricsPanel = lazy(() => import('../lyrics/LyricsPanel'));
const LyricsStrip = lazy(() => import('../lyrics/LyricsStrip'));
const LyricsSheet = lazy(() => import('../lyrics/LyricsSheet'));
// The same chunk DjLine loads for the medium rig.
const BitDock = lazy(() => import('./bit/BitDock'));

/** Where the lyrics go: null | 'stage' | 'side' | 'theater' | 'sheet'. */
function lyricsPlace({ on, current, narrow, isVideo, theater }) {
  if (!on || !current) return null;
  if (narrow) return 'sheet';
  if (!isVideo) return 'stage';
  return theater ? 'theater' : 'side';
}

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

/*
 * The large Bit's height: whatever the panel has left under the hero, between
 * --bit-h-large-min and --bit-h-large (the CSS clamps). He stands in the
 * foot row beside the recent tiles, so the row is as tall as the taller of
 * the two, and sizing him from the space left is what keeps that row from
 * ever making the panel scroll. The hero's height does not depend on the
 * row, so this cannot feed back on itself. Theater scrolls by design, so the
 * room there is negative and he takes the minimum.
 *
 * Written to the node, not to state, so a resize costs no render.
 */
function useBigRoom(footRef, scrollRef, heroRef, enabled) {
  useEffect(() => {
    const foot = footRef.current;
    const scroll = scrollRef.current;
    const hero = heroRef.current;
    if (!enabled || !foot || !scroll || !hero || typeof ResizeObserver !== 'function') return undefined;
    const measure = () => {
      const cs = getComputedStyle(scroll);
      const room = scroll.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom)
        - hero.offsetHeight - parseFloat(getComputedStyle(foot).marginTop);
      foot.style.setProperty('--big-room', `${Math.floor(room)}px`);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(scroll);
    ro.observe(hero);
    return () => ro.disconnect();
  }, [footRef, scrollRef, heroRef, enabled]);
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
  const { on: lyricsOn } = useLyrics();
  const narrow = useMediaQuery(BELOW_THEATER);
  const isPhone = useMediaQuery(PHONE);
  const { theme, bitSize } = useTheme();
  const colour = useArtColour(current?.artwork_url || '');
  const isVideo = mode === 'video';
  const lyrics = lyricsPlace({ on: lyricsOn, current, narrow, isVideo, theater });
  const scrollRef = useRef(null);
  const heroRef = useRef(null);
  const footRef = useRef(null);
  /*
   * Large Bit stands at the end of the foot row, after the recent tiles. The
   * phone has no foot row (its home list carries the recents), so there
   * Large is Medium: the rig beside the DJ line.
   */
  const big = bitSize === 'large' && !isPhone;
  useBigRoom(footRef, scrollRef, heroRef, big);

  const tint = colour
    ? `radial-gradient(90% 70% at 12% 0%, ${tintFor(colour.hue, theme)}, transparent 75%)`
    : null;
  const pool = colour ? `rgb(${colour.rgb.join(' ')})` : undefined;

  const cls = [
    styles.np,
    isVideo ? styles.isVideo : '',
    theater ? styles.isTheater : '',
    phoneOpen ? styles.phoneOpen : '',
    lyrics === 'stage' ? styles.isLyrics : '',
  ].filter(Boolean).join(' ');

  const overlay = lyrics === 'stage' || lyrics === 'theater' ? (
    <Suspense fallback={null}>
      {lyrics === 'stage' ? <LyricsPanel variant="stage" /> : <LyricsStrip />}
    </Suspense>
  ) : null;

  return (
    <main className={cls} aria-label="Now playing">
      <Tint background={tint} />

      <div className={styles.head}>
        <button type="button" className={`${ui.ib} ${styles.phoneOnly}`} onClick={onPhoneClose} aria-label="Minimise player">
          <Icon name="down" />
        </button>
        <div className={styles.headLeft}>
          <ModeSwitch className={styles.seg} />
          <LyricsButton />
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

      <div className={styles.scroll} ref={scrollRef}>
        <div className={styles.hero} ref={heroRef}>
          <ArtStage className={styles.stage} pool={pool} overlay={overlay} />

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

            {lyrics === 'side' || lyrics === 'theater' ? (
              <Suspense fallback={null}>
                <LyricsPanel variant={lyrics === 'side' ? 'side' : 'dock'} className={styles.lyricsSide} />
              </Suspense>
            ) : (
              <div className={styles.side}>
                <MoodSlider />
                {/* Hidden on a phone until opened: Bit holds still in here
                    while the home list's copy is the one in view. */}
                <DjLine bitPaused={isPhone && !phoneOpen} bigElsewhere={big} />
              </div>
            )}
          </div>
        </div>

        {/* One row: the last eight plays, then the large Bit right after the
            last of them (at the start when there are none). */}
        <div ref={footRef} className={styles.foot}>
          <RecentTrail variant="tiles" />
          {big && (
            <span className={styles.bigBit} aria-hidden="true">
              <Suspense fallback={null}><BitDock /></Suspense>
            </span>
          )}
        </div>
      </div>

      {lyrics === 'sheet' && (!isPhone || phoneOpen) && (
        <Suspense fallback={null}><LyricsSheet /></Suspense>
      )}
    </main>
  );
}
