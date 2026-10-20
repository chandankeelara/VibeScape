import { Suspense, lazy, useEffect, useLayoutEffect, useRef, useState } from 'react';
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
 * Whether the recent trail fits under the hero without the panel scrolling.
 * Everything else in the column is budgeted to fit a 720px-tall viewport;
 * the trail (~200px with its heading) only fits from about 900px tall, so it
 * is the one piece that gives way. Measured rather than a height media query because the hero's
 * height is content: a title that wraps, lyrics over the art.
 *
 * The trail's height (its wrapper, observed too, so its first tile landing
 * re-checks) is read whenever it is showing and remembered for when it is
 * not; TRAIL_EST, measured at 1440x900, stands in until it has shown once.
 * Hiding it changes neither the scroller nor the hero, so this cannot
 * oscillate.
 */
const TRAIL_EST = 198;
function useTrailFits(scrollRef, heroRef, trailRef, enabled) {
  const [fits, setFits] = useState(true);
  const trailH = useRef(TRAIL_EST);
  useEffect(() => {
    const scroll = scrollRef.current;
    const hero = heroRef.current;
    const trail = trailRef.current;
    if (!enabled || !scroll || !hero || !trail || typeof ResizeObserver !== 'function') {
      setFits(true);
      return undefined;
    }
    const check = () => {
      if (trail.offsetHeight) {
        trailH.current = trail.offsetHeight + parseFloat(getComputedStyle(trail).marginTop);
      }
      const cs = getComputedStyle(scroll);
      const room = scroll.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom)
        - hero.offsetHeight;
      setFits(room >= trailH.current);
    };
    const ro = new ResizeObserver(check);
    ro.observe(scroll);
    ro.observe(hero);
    ro.observe(trail);
    return () => ro.disconnect();
  }, [scrollRef, heroRef, trailRef, enabled]);
  return fits;
}

/*
 * The large Bit's box: the biggest square-ish corner (5:6, the rig's
 * viewBox) at the scroller's bottom-right that clears everything marked
 * data-bit-avoid, the art and the trail. He is absolute, so he never adds
 * height; this is what keeps him off the controls instead.
 *
 * For a box growing out of the corner, an obstacle is cleared either by
 * staying left of it or by staying below it, whichever allows more:
 *   h <= max((R - right) * 6/5, B - bottom)
 * and the corner gets the smallest such h. --bit-h-large caps it (the CSS
 * takes the min); under BIG_MIN he is too small to read as the big one and
 * hides, leaving the DJ line's icon.
 *
 * Written to the node, not to state: it runs after every render (the DJ
 * sentence's width is content) and must not cause one. Every obstacle is
 * observed as well, because a web font landing re-wraps the sentence with no
 * render and no change to the scroller or the hero (measured: the 1280x720
 * video layout kept a box sized for the fallback font, over the sentence).
 */
const BIG_GAP = 12;
const BIG_MIN = 72;
function useBigBitBox(bigRef, scrollRef, heroRef, trailRef, enabled) {
  const measure = useRef(() => {});
  const ro = useRef(null);
  const avoidList = () => {
    const hero = heroRef.current;
    if (!hero) return [];
    return [hero.firstElementChild, trailRef.current, ...hero.querySelectorAll('[data-bit-avoid]')].filter(Boolean);
  };
  measure.current = () => {
    const big = bigRef.current;
    const scroll = scrollRef.current;
    const hero = heroRef.current;
    if (!big || !scroll || !hero) return;
    const np = big.offsetParent;
    if (!np) return;
    const cs = getComputedStyle(scroll);
    const sr = scroll.getBoundingClientRect();
    const R = sr.left + scroll.clientLeft + scroll.clientWidth - parseFloat(cs.paddingRight);
    const B = sr.top + scroll.clientTop + scroll.clientHeight - parseFloat(cs.paddingBottom);
    let h = Infinity;
    for (const el of avoidList()) {
      const r = el.getBoundingClientRect();
      if (!r || !r.width || !r.height) continue;
      h = Math.min(h, Math.max((R - r.right - BIG_GAP) * 6 / 5, B - r.bottom - BIG_GAP));
    }
    const nr = np.getBoundingClientRect();
    big.style.right = `${nr.right - R}px`;
    big.style.bottom = `${nr.bottom - B}px`;
    big.style.setProperty('--fit-h', `${Math.max(0, Math.floor(h))}px`);
    big.dataset.fit = h >= BIG_MIN ? 'yes' : 'no';
  };
  useLayoutEffect(() => {
    if (!enabled) return;
    measure.current();
    // Observing an element twice is a no-op, so new obstacles join here.
    avoidList().forEach((el) => ro.current?.observe(el));
  });
  useEffect(() => {
    if (!enabled || typeof ResizeObserver !== 'function') return undefined;
    ro.current = new ResizeObserver(() => measure.current());
    [scrollRef.current, heroRef.current, ...avoidList()].forEach((el) => el && ro.current.observe(el));
    return () => { ro.current.disconnect(); ro.current = null; };
    // avoidList reads refs only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, scrollRef, heroRef, trailRef]);
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
  const trailRef = useRef(null);
  // Theater scrolls by design (the meta sits under the fold), and the phone
  // never shows the trail here.
  const trailFits = useTrailFits(scrollRef, heroRef, trailRef, !theater && !isPhone);
  /*
   * Large Bit needs the panel's bottom-right corner, which only a desktop
   * panel that does not scroll has: not theater (it scrolls by design), not
   * below 1024px (single column, or the phone), and not with lyrics in the
   * side column (they take the DJ line, and so Bit, with them).
   */
  const big = bitSize === 'large' && !theater && !narrow && lyrics !== 'side';
  const bigRef = useRef(null);
  useBigBitBox(bigRef, scrollRef, heroRef, trailRef, big);

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
            <div className={styles.info} data-bit-avoid="">
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
                <div data-bit-avoid=""><MoodSlider /></div>
                {/* Hidden on a phone until opened: Bit holds still in here
                    while the home list's copy is the one in view. */}
                <DjLine bitPaused={isPhone && !phoneOpen} bigElsewhere={big} />
              </div>
            )}
          </div>
        </div>

        <div ref={trailRef} className={`${styles.recent} ${trailFits ? '' : styles.recentOff}`}>
          <RecentTrail variant="tiles" />
        </div>
      </div>

      {big && (
        <span className={styles.bigBit} ref={bigRef} data-fit="no" aria-hidden="true">
          <Suspense fallback={null}><BitDock /></Suspense>
        </span>
      )}

      {lyrics === 'sheet' && (!isPhone || phoneOpen) && (
        <Suspense fallback={null}><LyricsSheet /></Suspense>
      )}
    </main>
  );
}
