/**
 * section#features.features — frontend/login.html:331-407.
 *
 * Nine cards in three weight tiers. The copy is verbatim; the two hero cards
 * keep their decorative visuals (a gradient slider and the five mood chips).
 */

import { memo } from 'react';

import { SectionHead, SerifEm } from './bits';
import { GITHUB_URL } from './links';
import styles from './Features.module.css';

const MOOD_CHIPS = [
  ['sleep', '#4c5b8a', false],
  ['chill', '#00b4d8', false],
  ['steady', '#7c3aed', true],
  ['hype', '#ec4899', false],
  ['beast', '#f43f5e', false],
];

function Tag({ num, children }) {
  return (
    <div className={styles.tag}>
      <span className={styles.tagNum}>{num}</span> {children}
    </div>
  );
}

function Features() {
  return (
    <section id="features" className={styles.features}>
      <SectionHead eyebrow="the player" lede="Real features shipping today. Not a roadmap.">
        Everything a music library <SerifEm>should</SerifEm> do — and doesn’t.
      </SectionHead>

      <div className={styles.grid}>
        <article className={`${styles.card} ${styles.heroCard}`}>
          <Tag num="01">Vibe slider</Tag>
          <h3>Drag from sleep to beast.</h3>
          <p>
            A single slider walks your entire library along an energy axis. Move it and the queue
            re-fills instantly with tracks that match — the color of the whole app shifts with your
            mood, from muted blue at chill to red at hype.
          </p>
          <div className={styles.visualSlider} aria-hidden="true">
            <div className={styles.fvTrack}>
              <span className={styles.fvFill} />
              <span className={styles.fvThumb} />
            </div>
            <div className={styles.fvTicks}>
              <span>sleep</span>
              <span>chill</span>
              <span>steady</span>
              <span>hype</span>
              <span>beast</span>
            </div>
          </div>
        </article>

        <article className={`${styles.card} ${styles.heroCard}`}>
          <Tag num="02">Five moods</Tag>
          <h3>One-tap mood filters.</h3>
          <p>
            Every track lands in one of five mood buckets — <em>sleep</em>, <em>chill</em>,{' '}
            <em>steady</em>, <em>hype</em>, <em>beast</em>. Tap one to lock the queue there.
          </p>
          <div className={styles.visualChips} aria-hidden="true">
            {MOOD_CHIPS.map(([label, color, active]) => (
              <span
                key={label}
                className={`${styles.fvChip} ${active ? styles.fvChipActive : ''}`}
                style={{ '--c': color }}
              >
                {label}
              </span>
            ))}
          </div>
        </article>

        <article className={styles.card}>
          <Tag num="03">Similar tracks</Tag>
          <h3>Neighbors that hold the vibe.</h3>
          <p>
            Every song surfaces eight neighbors from your library — ranked on four ML feature
            dimensions with a mood-match bonus so the next pick doesn’t derail the session.
          </p>
        </article>

        <article className={styles.card}>
          <Tag num="04">Search</Tag>
          <h3>Instant library search.</h3>
          <p>
            <kbd className={styles.kbd}>Ctrl</kbd> <kbd className={styles.kbd}>K</kbd> opens
            full-text search across title, artist, and album. Prefix matches rank first, so what
            you’re thinking of shows up first.
          </p>
        </article>

        <article className={styles.card}>
          <Tag num="05">Spotify sync</Tag>
          <h3>One-click library import.</h3>
          <p>
            Pull in your Liked Songs, Top Tracks, or hand-picked playlists. Or paste any public
            playlist URL. Progress streams live so you can watch it happen.
          </p>
        </article>

        <article className={`${styles.card} ${styles.tightCard}`}>
          <Tag num="06">Video mode</Tag>
          <p>
            Swap between the 30-second audio preview and the full YouTube video for any track. Find
            a different video if the auto-match isn’t right.
          </p>
        </article>

        <article className={`${styles.card} ${styles.tightCard}`}>
          <Tag num="07">One sign-in</Tag>
          <p>
            Your Spotify account is your VibeScape identity. No separate password, no PIN, no second
            profile to remember — sign in once, land in your library.
          </p>
        </article>

        <article className={`${styles.card} ${styles.tightCard}`}>
          <Tag num="08">Language-aware</Tag>
          <p>
            Whisper detects the sung language of every track — regional catalogs western APIs miss
            get labeled just as accurately. Instrumentals stay untagged rather than guessed.
          </p>
        </article>

        <article className={`${styles.card} ${styles.tightCard}`}>
          <Tag num="09">Free forever</Tag>
          <p>
            MIT-licensed on{' '}
            <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
              GitHub
            </a>
            . Runs on your machine or a $5 VM. No ads, no telemetry.
          </p>
        </article>
      </div>
    </section>
  );
}

export default memo(Features);
