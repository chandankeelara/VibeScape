/** section#mobile.split — frontend/login.html:276-323. */

import { memo } from 'react';

import { Eyebrow, SerifEm } from './bits';
import { NextIcon, PlayIcon, PrevIcon } from './icons';
import styles from './MobileSplit.module.css';

const BULLETS = [
  'Full-viewport album art backdrop',
  'Slide-up queue sheet',
  'Bluetooth & car-stereo transport controls',
  'Portrait-first controls that don’t shrink',
  'Works installed as a home-screen PWA',
];

function MobileSplit() {
  return (
    <section id="mobile" className={styles.split}>
      <div className={styles.copy}>
        <Eyebrow>mobile</Eyebrow>
        <h2>
          Built for the <SerifEm>phone</SerifEm> in your pocket.
        </h2>
        <p className={styles.lede}>
          On mobile, your album art becomes the whole screen. A single tap opens a slide-up queue.
          Bluetooth headphones and car stereos get play, pause, and skip through the Media Session
          API — no fumbling with the browser.
        </p>
        <ul className={styles.checkList}>
          {BULLETS.map((b) => (
            <li key={b}>{b}</li>
          ))}
        </ul>
      </div>

      <div className={styles.visual} aria-hidden="true">
        <div className={styles.frame}>
          <div className={styles.notch} />
          <div className={styles.screen}>
            <div className={styles.art} />
            <div className={styles.veil} />
            <div className={styles.meta}>
              <div className={styles.phoneTitle}>Late Night Drive</div>
              <div className={styles.phoneSub}>The Midnight</div>
              <div className={styles.phoneChips}>
                <span className={`${styles.phoneChip} ${styles.chipMood}`}>chill</span>
                <span className={`${styles.phoneChip} ${styles.chipVibe}`}>42</span>
              </div>
            </div>
            <div className={styles.bottom}>
              <div className={styles.bar}>
                <span />
              </div>
              <div className={styles.buttons}>
                <span className={styles.pbtn}>
                  <PrevIcon />
                </span>
                <span className={`${styles.pbtn} ${styles.pbtnLg}`}>
                  <PlayIcon />
                </span>
                <span className={styles.pbtn}>
                  <NextIcon />
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export default memo(MobileSplit);
