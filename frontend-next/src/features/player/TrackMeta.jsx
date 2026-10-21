import { usePlayer } from '../../state/PlayerContext';
import {
  trackVibe, moodFor, isMetadataOnly, classificationLabel, trackAccentStyle,
} from '../../lib/vibe';
import { languageLabel } from '../queue/labels';
import Icon from '../../ui/Icon';
import Equalizer from '../../ui/Equalizer';
import Marquee from '../../ui/Marquee';
import { HeartButton } from '../library';
import ui from '../../ui/controls.module.css';
import Skeleton from './Skeleton';
import styles from './TrackMeta.module.css';

/**
 * Title (one line, scrolling when it must) with its actions (the heart),
 * artist · album / genre, the badge row, and the two quiet actions (verify,
 * metrics). S2: content is loud, secondary items recede.
 *
 * The VIBE badge and the mood badge describe THIS track, so they are coloured
 * and named by the track's own score — not by the user's slider.
 */
export default function TrackMeta({ onShowMetrics, compact = false }) {
  const { current, source, loadingTrack, verifying, startVerify, stopVerify, canVerify } = usePlayer();

  // Checked BEFORE `current`, because during a track change `current` is still
  // the track that just finished — so without this the title would name the
  // previous song under a "picking" stage. Bar widths deliberately mismatch so
  // the block reads as text, not as a form. Every bar stands in the SAME box
  // the real line uses, quiet row included, so the column does not shrink and
  // regrow around a track change.
  if (loadingTrack) {
    return (
      <section className={`${styles.meta} ${compact ? styles.compact : ''}`} aria-busy="true">
        <p className={styles.overline}><Equalizer />Now playing</p>
        <div className={styles.titleRow}>
          <div className={styles.titleBox}><Skeleton w="64%" h="0.8em" /></div>
          <div className={styles.actions}><HeartButton track={null} /></div>
        </div>
        <div className={styles.byline}><Skeleton w="38%" h="16px" /></div>
        <p className={styles.genre} />
        <div className={styles.badges}>
          <Skeleton w="62px" h="22px" r="var(--r-sm)" />
          <Skeleton w="36px" h="22px" r="var(--r-sm)" />
          <Skeleton w="72px" h="22px" r="var(--r-sm)" />
        </div>
        <div className={styles.quiet} />
      </section>
    );
  }

  if (!current) {
    return (
      <section className={styles.meta}>
        <div className={styles.titleRow}>
          <div className={styles.titleBox}><h1 className={styles.title}><Marquee text="Move the slider to begin" /></h1></div>
        </div>
        <p className={styles.byline}>Pick a vibe and the DJ will find something.</p>
      </section>
    );
  }

  const vibe = trackVibe(current);
  const mood = vibe != null ? moodFor(vibe).name : null;
  const lang = current.language ? String(current.language).trim().slice(0, 4).toUpperCase() : '';

  return (
    <section className={`${styles.meta} ${compact ? styles.compact : ''}`}>
      <p className={styles.overline}><Equalizer />Now playing</p>
      {/* The title takes what the actions leave, and the actions are always
          the same buttons, so the box is one width whatever the song. */}
      <div className={styles.titleRow}>
        <div className={styles.titleBox}>
          <h1 className={styles.title}><Marquee text={current.title || 'Untitled'} /></h1>
        </div>
        <div className={styles.actions}>
          <HeartButton track={current} />
        </div>
      </div>
      <p className={styles.byline} title={[current.artist, current.album].filter(Boolean).join(' · ') || undefined}>
        <span className={styles.artist}>{current.artist || 'Unknown artist'}</span>
        {current.album && (
          <>
            <span className={styles.sep} aria-hidden="true">·</span>
            <span className={styles.album}>{current.album}</span>
          </>
        )}
      </p>
      {/* Rendered when empty too: the line's box is part of the fixed column. */}
      <p className={styles.genre}>{current.genre || ''}</p>

      <div className={styles.badges}>
        {vibe != null ? (
          <span className={`${ui.badge} ${ui.badgeVibe}`} style={trackAccentStyle(vibe)} title="Measured energy">
            VIBE {vibe}
          </span>
        ) : (
          <span className={ui.badge} title="Not analysed yet">Analysing</span>
        )}
        {lang && <span className={ui.badge} title={languageLabel(current.language)}>{lang}</span>}
        {mood && <span className={`${ui.badge} ${ui.badgeMood}`}>{mood}</span>}
        {source === 'spotify' ? (
          <span className={ui.badge} title="Full track via Spotify">
            <span className={ui.badgeDot} aria-hidden="true" />FULL · Spotify
          </span>
        ) : source ? (
          <span className={ui.badge} title="30-second preview">30s PREVIEW</span>
        ) : null}
        {isMetadataOnly(current) && (
          <span className={ui.badge} title="No local audio — playable only via Spotify Premium">SDK only</span>
        )}
      </div>

      <div className={styles.quiet}>
        {/* Plays the exact 30s clip the model was given for this track. */}
        <button
          type="button"
          className={`${styles.qbtn} ${verifying ? styles.qbtnOn : ''}`}
          onClick={verifying ? stopVerify : startVerify}
          disabled={!canVerify && !verifying}
          aria-pressed={verifying}
          title={
            canVerify
              ? `Play the 30-sec clip used to classify this track — ${classificationLabel(current.classification_source)}`
              : 'No classification audio available for this track'
          }
        >
          <Icon name="verify" size={16} />
          <span>{verifying ? 'Stop clip' : 'Verify match'}</span>
        </button>
        {onShowMetrics && (
          <button type="button" className={styles.qbtn} onClick={onShowMetrics} title="Track metrics (Ctrl+Shift+M)">
            <Icon name="chart" size={16} />
            <span>Metrics</span>
          </button>
        )}
      </div>
    </section>
  );
}
