import { usePlayer } from '../../state/PlayerContext';
import TransportButtons from './TransportButtons';
import ModeToggle from './ModeToggle';
import TheaterToggle from './TheaterToggle';
import PipToggle from './PipToggle';
import Skeleton from './Skeleton';
import styles from './TheaterBar.module.css';

/**
 * The theater-mode strip: one short bar under the video.
 *
 * Replaces the whole meta column (TrackMeta + Transport + MoodSlider) while
 * theater is on, and every pixel it doesn't use goes to the video — which is
 * the entire point of the mode. See TheaterBar.module.css for the numbers.
 *
 * WHAT IS DELIBERATELY NOT HERE
 *
 * - Mood slider, vibe number, mood ticks. The user's call. Nothing breaks:
 *   MoodSlider is a purely controlled consumer of `vibe`/`mood` from
 *   PlayerContext with no local state, no refs and no registration — the
 *   accent variables are written by applyAccent() in the context, not by the
 *   component — so it can be unmounted and remounted freely, and when it
 *   comes back on leaving theater it renders whatever the vibe is by then.
 *   Verified that nothing in state/, media/ or lib/ reaches for it in the DOM.
 * - Vibe / language / sdk-only pills and the mood, vibe, source, verify and
 *   metrics chips. "Little details", not a spec sheet.
 * - The progress bar and clock. In theater the player is ALWAYS the YouTube
 *   iframe, and it carries its own seek bar, elapsed time, volume and
 *   fullscreen a few pixels above this strip. A second seek bar would be the
 *   duplicate-control drift we just removed everywhere else. Seeking from the
 *   keyboard (Home / End / PgUp / PgDn) is untouched.
 *
 * KEPT: title, artist, album. Album earns its line because it is the one
 * field that disambiguates a title+artist pair (live versions, remasters,
 * singles vs the album cut) and it costs nothing — it shares one line and is
 * the first thing to be ellipsed.
 */
export default function TheaterBar({
  className = '',
  theater,
  onToggleTheater,
  pipSupported = false,
  pipOpen = false,
  pipAuto = false,
  onTogglePip,
}) {
  const { current, loadingTrack } = usePlayer();

  return (
    <section className={`${styles.bar} ${className}`}>
      <div className={styles.text}>
        {loadingTrack ? (
          <>
            <Skeleton w="42%" h="clamp(17px, 2.2dvh, 22px)" />
            <Skeleton w="26%" h="clamp(11.5px, 1.4dvh, 13px)" />
          </>
        ) : (
          <>
            <h1 className={styles.title}>{current?.title || 'Nothing playing'}</h1>
            <p className={styles.detail}>
              <span className={styles.artist}>{current?.artist || 'Unknown artist'}</span>
              {current?.album && (
                <>
                  <span className={styles.sep} aria-hidden="true">·</span>
                  <span className={styles.album}>{current.album}</span>
                </>
              )}
            </p>
          </>
        )}
      </div>

      {/* Same components Transport uses — never a second copy. */}
      <div className={styles.controls}>
        <TransportButtons compact />
        <ModeToggle className={styles.modeSlot} />
        {/* Theater replaces the whole meta column, so the miniplayer toggle
            has to come with it or the control vanishes in the one mode where
            the user is most likely to tab away. Same cluster, immediately
            before theater's exit. */}
        {pipSupported && (
          <PipToggle
            open={pipOpen}
            auto={pipAuto}
            onToggle={onTogglePip}
            className={styles.pipSlot}
          />
        )}
        {/*
          * Theater's EXIT lives here, at the bottom-right of the video region,
          * roughly where its entrance was on the card above. It does not need
          * ArtStage's dock() guard: that guard exists for ENTERING theater
          * with a detached frame, and detached cannot be true while theater
          * is — the two are mutually exclusive by construction.
          */}
        <TheaterToggle
          theater={theater}
          onToggle={onToggleTheater}
          className={styles.theaterSlot}
        />
      </div>
    </section>
  );
}
