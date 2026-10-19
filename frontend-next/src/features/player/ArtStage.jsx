import { useEffect, useRef, useState } from 'react';
import { usePlayer, useVerifyCountdown } from '../../state/PlayerContext';
import { MOUNT_ID, createPlayer } from '../../media/youtube';
import { classificationLabel } from '../../lib/vibe';
import Icon from '../../ui/Icon';
import ui from '../../ui/controls.module.css';
import styles from './ArtStage.module.css';

/**
 * The stage: album art, OR the YouTube video in video mode.
 *
 * CRITICAL: the #ytPlayer mount div is rendered UNCONDITIONALLY and is never
 * keyed or conditionally unmounted. YT.Player replaces a node inside it; if
 * React ever recreated it, playback would die mid-song. Video mode, theater
 * and the phone now-playing screen are all RESTYLES of this same node — the
 * video surface only changes visibility. See src/media/README.md.
 *
 * The stage itself is square in audio mode and 16:9 in video mode; the parent
 * (NowPlaying) sizes it. `pool` is the art's own colour, thrown as a soft
 * light under the cover instead of a black drop shadow.
 *
 * `overlay` is laid over the stage by the parent — the lyrics, over the art
 * or as subtitles on the video. It is a SIBLING after the video surface and
 * positions itself, so adding or removing it never touches #ytPlayer.
 */

/** Shown while the classification clip plays; owns the ~4x/sec countdown. */
function VerifyOverlay({ track }) {
  const { active, remainingMs } = useVerifyCountdown();
  if (!active) return null;
  const secs = Math.max(0, Math.ceil(remainingMs / 1000));
  return (
    <div className={styles.verify} aria-live="polite">
      <Icon name="verify" size={22} />
      <b>Classification audio</b>
      <span>{classificationLabel(track?.classification_source)} · {secs}s</span>
    </div>
  );
}

/**
 * Cover with a gentle crossfade on track change. Two layers at most: the
 * outgoing one stays underneath until the incoming one has faded in.
 */
function Cover({ url }) {
  const [layers, setLayers] = useState([]);
  const seq = useRef(0);

  useEffect(() => {
    if (!url) { setLayers([]); return undefined; }
    let cancelled = false;
    const img = new Image();
    img.onload = () => {
      if (cancelled) return;
      const id = ++seq.current;
      setLayers((prev) => [...prev.slice(-1), { url, id }]);
    };
    img.onerror = () => { if (!cancelled) setLayers([]); };
    img.src = url;
    return () => { cancelled = true; };
  }, [url]);

  // Drop the outgoing layer once the incoming fade has finished.
  useEffect(() => {
    if (layers.length < 2) return undefined;
    const t = setTimeout(() => setLayers((prev) => prev.slice(-1)), 650);
    return () => clearTimeout(t);
  }, [layers]);

  if (!layers.length) {
    return (
      <div className={styles.coverEmpty} aria-hidden="true">
        <Icon name="disc" size={42} />
      </div>
    );
  }
  return layers.map((l, i) => (
    <img
      key={l.id}
      className={`${styles.coverLayer} ${i === layers.length - 1 && layers.length > 1 ? styles.coverIn : ''}`}
      src={l.url}
      alt=""
    />
  ));
}

export default function ArtStage({ className = '', pool, overlay = null }) {
  const { current, mode, videoState, loadingTrack, setPlaybackMode } = usePlayer();
  const isVideo = mode === 'video';
  const noVideo = isVideo && videoState === 'unavailable';

  // The mount node exists from first render; ask the SDK to bind once it's up.
  useEffect(() => { createPlayer(); }, []);

  const cls = [
    styles.stage,
    isVideo ? styles.isVideo : '',
    noVideo ? styles.noVideo : '',
    loadingTrack ? styles.loading : '',
    className,
  ].filter(Boolean).join(' ');

  return (
    <div className={cls} style={pool ? { '--pool': pool } : undefined}>
      <div className={styles.art}>
        <Cover url={current?.artwork_url || ''} />
      </div>

      <div className={styles.video} aria-label="Music video">
        {videoState === 'loading' && (
          <div className={styles.videoNote} role="status">
            <span className={ui.dots} aria-hidden="true"><i /><i /><i /></span>Loading video
          </div>
        )}
        {/* Never key, never conditionally render. React owns this div;
            YT.Player replaces an inner node it creates itself (see
            media/youtube.js TARGET_ID). */}
        <div id={MOUNT_ID} className={styles.ytMount} />
      </div>

      <div className={styles.novideo}>
        <div>
          <Icon name="novideo" size={32} />
          <h3>No video for this track</h3>
          <p>We couldn&apos;t find an official video.</p>
          <button type="button" className={ui.pill} onClick={() => setPlaybackMode('audio')}>
            <Icon name="music" size={16} />Listen to audio
          </button>
        </div>
      </div>

      {overlay}

      <VerifyOverlay track={current} />

      {loadingTrack && (
        <div className={styles.picking} role="status">
          <span className={ui.dots} aria-hidden="true"><i /><i /><i /></span>
          DJ is picking the next track
        </div>
      )}
    </div>
  );
}
