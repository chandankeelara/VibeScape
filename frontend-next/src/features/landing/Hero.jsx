/**
 * section.hero — frontend/login.html:135-271.
 *
 * Left: the editorial headline and the two CTAs. Right: the live vibe demo,
 * which is the whole product in one card. `useVibeDemo` owns the behaviour
 * ported from login.js; this file is structure only.
 */

import { LADDER } from './vibe';
import { Eyebrow, SerifEm } from './bits';
import { LoginIcon, PlayIcon } from './icons';
import styles from './Hero.module.css';

const PROOF = [
  ['5', 'moods'],
  ['1', 'click sign-in'],
  ['0', 'passwords'],
  ['0', 'ads'],
];

export default function Hero({ demo, onLogin, onGuest, guestPending }) {
  const { vibe, mood, demo: track, setVibe, nudge, hoverProps, cursorY, refs } = demo;

  // Arrow keys nudge the slider from anywhere inside the demo card. Scoped to
  // the card on purpose: legacy bound this to `document` and called
  // preventDefault, which stole arrow-key page scrolling for the whole page.
  // The range input handles its own arrows natively, so skip it here.
  function onKeyDown(ev) {
    if (ev.target === refs.sliderRef.current) return;
    if (ev.key !== 'ArrowUp' && ev.key !== 'ArrowDown') return;
    ev.preventDefault();
    const step = ev.shiftKey ? 10 : 5;
    nudge(ev.key === 'ArrowUp' ? step : -step);
  }

  return (
    <section className={styles.hero}>
      <div className={styles.copy}>
        <Eyebrow>mood-based music player</Eyebrow>

        <h1 className={styles.h1}>
          Play music in{' '}
          <span className={styles.moodSwap}>
            {/* `key` restarts the fade-in keyframes on every mood change —
                the legacy page forced a reflow to do the same thing. */}
            <SerifEm key={mood} className={styles.heroSerif}>
              {mood}
            </SerifEm>
          </span>{' '}
          mode.
        </h1>

        <p className={styles.lede}>
          Every song, scored for energy and mood. Drag the slider — your queue shifts with it. Sign
          in with Spotify to sort your own library, or start listening in one tap.
        </p>

        <div className={styles.ctaStack} role="group" aria-label="Sign in options">
          <button className={`${styles.cta} ${styles.ctaPrimary}`} type="button" onClick={onLogin}>
            <span className={styles.ctaIcon} aria-hidden="true">
              <LoginIcon />
            </span>
            <span className={styles.ctaLabel}>
              <span className={styles.ctaTitle}>Log in</span>
              <span className={styles.ctaSub}>Spotify, email — more providers soon</span>
            </span>
            <span className={styles.ctaArrow} aria-hidden="true">
              →
            </span>
          </button>

          <button
            className={`${styles.cta} ${styles.ctaQuiet}`}
            type="button"
            onClick={onGuest}
            disabled={guestPending}
          >
            <span className={styles.ctaIcon} aria-hidden="true">
              <PlayIcon />
            </span>
            <span className={styles.ctaLabel}>
              <span className={styles.ctaTitle}>
                {guestPending ? 'Starting demo…' : 'Just listen'}
              </span>
              <span className={styles.ctaSub}>
                No signup — curated library, streamed from YouTube
              </span>
            </span>
            <span className={styles.ctaArrow} aria-hidden="true">
              →
            </span>
          </button>

          <p className={styles.ctaNote}>
            No passwords required when you use Spotify. Your identity is <em>yours</em> — guest
            sessions are ephemeral.
          </p>
        </div>

        <ul className={styles.proof} aria-label="At a glance">
          {PROOF.map(([num, label]) => (
            <li key={label}>
              <span className={styles.proofNum}>{num}</span>
              <span className={styles.proofLbl}>{label}</span>
            </li>
          ))}
        </ul>
      </div>

      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <aside
        className={styles.demo}
        aria-label="Live vibe demo"
        style={{ '--slider-pct': `${vibe}%` }}
        onKeyDown={onKeyDown}
        {...hoverProps}
      >
        <div className={styles.npCard}>
          <div className={styles.npArt} aria-hidden="true">
            <div className={styles.npArtInner} />
          </div>
          <div className={styles.npBody}>
            <div className={styles.npTitle}>{track.title}</div>
            <div className={styles.npSub}>{track.sub}</div>
            <div className={styles.npChips}>
              <span className={`${styles.npChip} ${styles.npChipMood}`}>{mood}</span>
              <span className={`${styles.npChip} ${styles.npChipVibe}`}>{vibe}/100</span>
              <span className={`${styles.npChip} ${styles.npChipGenre}`}>synthwave</span>
            </div>
            <div className={styles.npProgress} aria-hidden="true">
              <span className={styles.npProgressFill} />
            </div>
          </div>
        </div>

        <div className={styles.ladder}>
          <div className={styles.grid} ref={refs.gridRef}>
            <div className={`${styles.gridLabel} ${styles.gridLabelTop}`}>energy ↑</div>
            {LADDER.map((band) => {
              const active = band.mood === mood;
              return (
                <div
                  key={band.mood}
                  className={`${styles.cell} ${active ? styles.cellActive : ''}`}
                  ref={active ? refs.activeCellRef : null}
                >
                  {band.mood}
                </div>
              );
            })}
            <div className={`${styles.gridLabel} ${styles.gridLabelBot}`}>energy ↓</div>
            <span
              className={styles.cursor}
              aria-hidden="true"
              style={{ transform: `translateY(${cursorY}px)` }}
            />
          </div>

          <div className={styles.sliderWrap}>
            <div className={styles.heroNum}>
              <span className={styles.num}>{vibe}</span>
              <span className={styles.word}>{mood}</span>
            </div>
            <div className={styles.sliderTrack} ref={refs.trackRef}>
              <input
                ref={refs.sliderRef}
                className={styles.slider}
                type="range"
                min="0"
                max="100"
                step="1"
                value={vibe}
                onChange={(e) => setVibe(Number(e.target.value))}
                aria-label="Vibe — drag to change mood"
                aria-valuetext={`${vibe} of 100 — ${mood}`}
              />
            </div>
            <div className={styles.hint} aria-hidden="true">
              <kbd>↑</kbd>
              <kbd>↓</kbd>
              <span>drag</span>
            </div>
          </div>
        </div>
      </aside>
    </section>
  );
}
