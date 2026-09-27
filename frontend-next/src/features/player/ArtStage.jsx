import { useEffect, useRef, useState } from 'react';
import { usePlayer } from '../../state/PlayerContext';
import { MOUNT_ID, createPlayer } from '../../media/youtube';
import useVideoFrame, { RESIZE_DIRS } from './useVideoFrame';
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
export default function ArtStage() {
  const { current, mode, videoState } = usePlayer();
  const [artLoaded, setArtLoaded] = useState(false);
  const frameRef = useRef(null);
  const { detached, dock, dragProps, resizeProps } = useVideoFrame(frameRef);

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
    <section className={styles.wrap}>
      <div className={styles.glow} aria-hidden="true" />

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

          <div className={styles.videoInner}>
            {/* Drag bar. Grabbing anywhere on the video also drags (dragSurface
                below), matching legacy. */}
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

            {/* Never key, never conditionally render — YT.Player owns this node
                and React recreating it would kill playback mid-song. */}
            <div id={MOUNT_ID} className={styles.ytMount} />

            {/* Transparent grab layer over the iframe. The iframe swallows
                pointer events, so without this you could only drag by the bar. */}
            <div className={styles.dragSurface} aria-hidden="true" title="Drag to move" {...dragProps} />
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
