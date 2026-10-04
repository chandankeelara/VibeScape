import { useEffect, useRef, useState } from 'react';
import { usePlayer, useVerifyCountdown } from '../../state/PlayerContext';
import { MOUNT_ID, createPlayer } from '../../media/youtube';
import { classificationLabel } from '../../lib/vibe';
import useVideoFrame, { RESIZE_DIRS } from './useVideoFrame';
import TheaterToggle from './TheaterToggle';
import DjSearching from './DjSearching';
import styles from './ArtStage.module.css';

/**
 * Album art + the YouTube video stage.
 *
 * CRITICAL: the #ytPlayer mount div is rendered UNCONDITIONALLY and is never
 * keyed or conditionally unmounted. YT.Player replaces that node with an
 * <iframe>; if React ever recreates it, playback dies mid-song and the player
 * instance is orphaned. Video mode toggles VISIBILITY only (see .videoHidden).
 * See src/media/README.md.
 */
/**
 * Shown while the classification clip plays. Subscribes to the countdown on
 * its own so the ~4x/sec tick doesn't re-render the rest of the player.
 */
function VerifyOverlay({ track }) {
  const { active, remainingMs } = useVerifyCountdown();
  if (!active) return null;
  const secs = Math.max(0, Math.ceil(remainingMs / 1000));
  return (
    <div className={styles.verifyOverlay} aria-live="polite">
      <div className={styles.verifyIcon} aria-hidden="true">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 12a9 9 0 0 1 18 0v5a2 2 0 0 1-2 2h-2v-7h4" />
          <path d="M3 12v5a2 2 0 0 0 2 2h2v-7H3" />
        </svg>
      </div>
      <div className={styles.verifyTitle}>classification audio</div>
      <div className={styles.verifySub}>
        {classificationLabel(track?.classification_source)} · {secs}s
      </div>
    </div>
  );
}

export default function ArtStage({
  theater = false,
  canTheater = false,
  onToggleTheater,
  onExitTheater,
}) {
  const { current, mode, videoState, loadingTrack } = usePlayer();
  const [artLoaded, setArtLoaded] = useState(false);
  const frameRef = useRef(null);
  // Starting a drag or a resize stands theater down — see below.
  const { detached, dock, dragProps, resizeProps } = useVideoFrame(frameRef, {
    onDetach: onExitTheater,
  });

  /*
   * Theater vs detached: mutually exclusive, enforced in both directions.
   *
   * They are two answers to the same wish ("make the video bigger") and they
   * contradict each other mechanically: a detached frame is position:fixed,
   * so it leaves the grid, and theater would then be widening an empty slot
   * under a floating panel. Rules:
   *
   *   entering theater -> dock() a detached frame (and dock() clears the
   *                       parked rect, so the two storage keys can never both
   *                       read "on" after this)
   *   detaching        -> onExitTheater() (wired above)
   *
   * Because both writes land in the same gesture, an inconsistent pair can
   * only arrive from storage written before this feature existed (or edited
   * by hand). The effect below is the invariant that catches it: detach wins,
   * because the parked rect is positional data we would destroy, while
   * theater is a boolean the user re-sets with one click.
   *
   * It is deliberately NOT mount-only. A stale pair cannot be seen at mount:
   * `theater` also requires video mode, and the player boots in audio, so the
   * conflict only becomes visible on the first switch to video.
   */
  const onTheaterClick = () => {
    // Belt and braces. The control is not rendered while detached (see the
    // strip below), so this branch should be unreachable from the UI — it
    // stays because it is where the rule is stated.
    if (!theater && detached) dock();
    onToggleTheater?.();
  };

  /*
   * The in-card chrome strip exists only where it is free and useful:
   * - `canTheater` covers video mode AND >=1024px, from useTheaterMode. It is
   *   a prop rather than a media query because the strip RESERVES HEIGHT off
   *   the player, so a narrow viewport must not reserve it for a control that
   *   could do nothing. Audio mode still HIDES the control rather than
   *   disabling it — the whole video stage is not on screen there.
   * - not in theater, where the card IS 16:9 and a strip would cost ~8% of
   *   the video's width; there the toggle moves to TheaterBar's control
   *   cluster, directly beneath the video
   * - not while detached, where the frame can be dragged smaller than the
   *   strip is useful in
   */
  const showCardChrome = canTheater && !theater && !detached;

  useEffect(() => {
    if (theater && detached) onExitTheater?.();
  }, [theater, detached, onExitTheater]);

  const artUrl = current?.artwork_url || '';

  // Preload so we swap in one step rather than flashing a broken image, and
  // mirror onto a CSS var for the blurred mobile backdrop (legacy app.js:1207).
  useEffect(() => {
    setArtLoaded(false);
    if (!artUrl) {
      document.body.style.removeProperty('--art-url');
      return;
    }
    const img = new Image();
    img.onload = () => {
      setArtLoaded(true);
      document.body.style.setProperty('--art-url', `url('${artUrl.replace(/'/g, "\\'")}')`);
    };
    img.onerror = () => {
      setArtLoaded(false);
      document.body.style.removeProperty('--art-url');
    };
    img.src = artUrl;
  }, [artUrl]);

  // The mount node exists from first render; ask the SDK to bind once it's up.
  useEffect(() => { createPlayer(); }, []);

  return (
    <section className={`${styles.wrap} ${theater ? styles.wrapTheater : ''}`}>
      <div className={styles.glow} aria-hidden="true" />

      <VerifyOverlay track={current} />

      {/* Audio mode only: in video mode the art is hidden anyway and the
          video stage runs its own "Loading video…" overlay, so showing this
          here would stack two spinners over the same card. */}
      {loadingTrack && mode !== 'video' && <DjSearching />}

      <div className={`${styles.art} ${mode === 'video' ? styles.artHidden : ''}`}>
        {artLoaded ? (
          <img className={styles.artImg} src={artUrl} alt="" />
        ) : (
          <div className={styles.artEmpty} aria-hidden="true">
            <svg viewBox="0 0 24 24" width="42" height="42" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="3" />
            </svg>
          </div>
        )}
      </div>

      <div
        className={`${styles.videoStage} ${mode === 'video' ? '' : styles.videoHidden} ${detached ? styles.stageDetached : ''}`}
      >
        <div ref={frameRef} className={`${styles.videoFrame} ${detached ? styles.frameDetached : ''}`}>
          {/* Same accent glow as the album art, so the video card reads as the
              same object. Driven by --art-glow-alpha / --vibe-accent. */}
          <div className={styles.videoGlow} aria-hidden="true" />

          <div className={`${styles.videoInner} ${showCardChrome ? styles.innerChrome : ''}`}>
            {/* The ONLY drag origin. A transparent grab layer over the video
                would swallow every pointerdown, and a cross-origin iframe can
                never be handed that click back — so YouTube's own controls
                (play/pause, seek, volume, fullscreen) would all be dead.
                Legacy reached the same conclusion: its .video-drag-surface is
                display:none. Pointer capture on this bar means a drag started
                here still tracks across the whole video. */}
            <div className={styles.videoTopbar} title="Drag to move" {...dragProps}>
              <span className={styles.grip} aria-hidden="true"><span /><span /><span /></span>
              <span className={styles.topbarHint}>
                Drag to move · grab any edge or corner to resize
              </span>
              {detached && (
                <button
                  className={styles.dockBtn}
                  type="button"
                  onClick={dock}
                  aria-label="Snap back to original position"
                  title="Snap back to original position"
                >
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="15 3 21 3 21 9" /><polyline points="9 21 3 21 3 15" />
                    <line x1="21" y1="3" x2="14" y2="10" /><line x1="3" y1="21" x2="10" y2="14" />
                  </svg>
                </button>
              )}
            </div>

            {videoState === 'loading' && (
              <div className={styles.videoOverlay}>
                <div className={styles.spinner} aria-hidden="true" />
                <p>Loading video…</p>
              </div>
            )}
            {videoState === 'unavailable' && (
              <div className={styles.videoOverlay}>
                <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <rect x="2" y="6" width="20" height="12" rx="2" />
                  <polygon points="10 9 15 12 10 15 10 9" />
                  <line x1="3" y1="3" x2="21" y2="21" />
                </svg>
                <p>No video for this track</p>
              </div>
            )}

            {/* Never key, never conditionally render. React owns this div;
                YT.Player replaces an inner node it creates itself (see
                media/youtube.js TARGET_ID). Letting YT replace THIS div made
                React's fiber point at a detached node, and the next time one
                of the overlays above mounted, insertBefore threw
                NotFoundError. */}
            <div id={MOUNT_ID} className={styles.ytMount} />

            {/*
              * Card chrome, BELOW the player surface — never over it.
              *
              * The user asked for the theater button bottom-right, like
              * YouTube's. Bottom-right of the player surface is exactly where
              * YouTube's OWN bottom-right controls are (fullscreen, settings,
              * miniplayer, its own theater button), and we cannot overlay a
              * cross-origin iframe without swallowing the pointerdown we can
              * never hand back — the same reason the legacy drag surface is
              * display:none. Offsetting above their control row is not
              * reliable either: its height scales with the player and it
              * auto-hides, and we cannot measure it across the origin.
              *
              * So this strip takes a slice of the CARD, under the iframe, and
              * is bottom-right of the card without ever touching the player.
              * In this (non-theater) mode the card is SQUARE and the 16:9
              * video is already letterboxed inside it with room to spare, so
              * the slice costs zero picture — it eats existing black.
              *
              * It carries no drag handler, so a click here cannot start a
              * frame drag. (.videoTopbar's handler skips `button` targets,
              * but that guard no longer has to cover this control at all.)
              *
              * Hidden while DETACHED: the frame can be dragged down to
              * 240x160, where a 52px strip would be a third of it — and
              * theater and detached are mutually exclusive anyway, so the
              * way to theater from a detached frame is the dock button above.
              */}
            {showCardChrome && (
              <div className={styles.videoBottombar}>
                <TheaterToggle theater={theater} onToggle={onTheaterClick} />
              </div>
            )}
          </div>

          {RESIZE_DIRS.map((dir) => (
            <div
              key={dir}
              className={`${styles.resize} ${styles[`resize_${dir}`]}`}
              data-resize={dir}
              aria-hidden="true"
              {...resizeProps}
            />
          ))}
        </div>
      </div>
    </section>
  );
}
