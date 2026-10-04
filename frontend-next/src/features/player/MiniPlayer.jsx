import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { usePlayer } from '../../state/PlayerContext';
import TransportButtons from './TransportButtons';
import styles from './MiniPlayer.module.css';

/**
 * The picture-in-picture miniplayer — a SECOND VIEW of the one player, never a
 * second player.
 *
 * STATE SHARING is the whole design. This component calls the same
 * `usePlayer()` as the main transport, and renders the same
 * `TransportButtons`, so `togglePlay` / `next` / `prev` here are literally the
 * same closures the main UI calls. Nothing is mirrored, synced or duplicated,
 * which means:
 *
 *   - the media-session metadata/handlers in media/player.js are untouched;
 *   - a skip from this window goes through PlayerContext.next() exactly as one
 *     from the main transport does, so listenLog sees the same transition and
 *     stamps the same `source` ('queue' / 'dj' / 'autoplay') and the same
 *     `reason` ('skipped'). Attribution could only drift if this window had
 *     its own controls, which is why it does not.
 *
 * WHY A PORTAL. React attaches its full listener set directly to a portal
 * CONTAINER (`preparePortalMount` -> `listenToAllSupportedEvents`), not only to
 * the app's root container — so clicks inside another document still reach
 * React's synthetic event system. That is what makes `createPortal` the right
 * tool for Document PiP rather than a second `createRoot`, which would give
 * the window its own React tree and therefore its own copy of every provider.
 *
 * VIDEO MODE shows artwork, not video. The YouTube iframe cannot come with us:
 * moving an element into the PiP window reparents it, and reparenting an
 * iframe RELOADS it — the video would restart from zero and the YT.Player
 * instance would be orphaned (the same hazard ArtStage.jsx documents around
 * #ytPlayer). So the video keeps playing in the tab and this window carries
 * the metadata and the transport, with a line saying so. YouTube's own
 * context menu still offers real video PiP for anyone who wants the picture.
 */
export default function MiniPlayer({ pipWindow }) {
  const { current, mode, vibe, mood } = usePlayer();
  const [artFailed, setArtFailed] = useState(false);

  const artUrl = current?.artwork_url || '';
  useEffect(() => { setArtFailed(false); }, [artUrl]);

  /*
   * The accent variables are written as INLINE STYLE on the main document's
   * <html> by applyAccent() (lib/vibe.js) — inline style is not in any
   * stylesheet, so copyStyles() cannot bring it across and the miniplayer
   * would fall back to tokens.css's default purple.
   *
   * Re-read from the live inline style (rather than recomputing the accent
   * here) so there is still exactly one writer and no chance of the two views
   * disagreeing. Keyed on `vibe`, which is the only thing that moves it — this
   * runs on a slider release, not on a frame.
   *
   * --art-glow-alpha is deliberately NOT mirrored: it is written ~60x/sec by
   * the analyser loop and nothing in this window reads it.
   */
  useEffect(() => {
    if (!pipWindow) return;
    const from = document.documentElement.style;
    const to = pipWindow.document.documentElement.style;
    for (const name of ['--vibe-accent', '--vibe-accent-2']) {
      const v = from.getPropertyValue(name);
      if (v) to.setProperty(name, v);
    }
  }, [pipWindow, vibe]);

  if (!pipWindow) return null;

  return createPortal(
    <div className={styles.mini}>
      <div className={styles.art}>
        {artUrl && !artFailed ? (
          <img
            className={styles.artImg}
            src={artUrl}
            alt=""
            onError={() => setArtFailed(true)}
          />
        ) : (
          <div className={styles.artEmpty} aria-hidden="true">
            <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="3" />
            </svg>
          </div>
        )}
      </div>

      <div className={styles.body}>
        <div className={styles.text}>
          <p className={styles.title} title={current?.title || ''}>
            {current?.title || 'Nothing playing'}
          </p>
          <p className={styles.artist} title={current?.artist || ''}>
            {current?.artist || 'Unknown artist'}
          </p>
        </div>

        <div className={styles.row}>
          {/*
            * The vibe as a READOUT, not a control. It earns the space because
            * it is what the DJ will pick from next, so it answers "why this
            * song" without the user going back to the tab — but a 40px fader
            * in a 400px window would be a mis-click waiting to happen, and
            * nothing else here authors anything either. No queue, no search,
            * no seek bar.
            */}
          <p className={styles.vibe} aria-label={`Vibe ${vibe}, ${mood}`}>
            <span className={styles.vibeNum}>{vibe}</span>
            <span className={styles.vibeMood}>{mood}</span>
          </p>

          <div className={styles.controls}>
            {/* The SAME component the main transport renders. */}
            <TransportButtons compact />
          </div>
        </div>

        {mode === 'video' && (
          <p className={styles.note}>video keeps playing in the tab</p>
        )}
      </div>
    </div>,
    pipWindow.document.body,
  );
}
