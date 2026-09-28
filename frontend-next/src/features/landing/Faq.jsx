/**
 * section#faq.faq — frontend/login.html:429-476.
 *
 * Native <details>/<summary>, same as legacy: real disclosure semantics for
 * free (expanded state, Enter/Space, find-in-page opening a closed answer).
 */

import { memo } from 'react';

import { SectionHead } from './bits';
import { GITHUB_URL } from './links';
import styles from './Faq.module.css';

const ITEMS = [
  {
    q: 'How does VibeScape sort music by mood?',
    a: (
      <>
        Every track in your library is scored for energy using a fine-tuned audio-ML model. That
        score places it in one of five mood buckets — sleep, chill, steady, hype, or beast — which
        become one-tap filters. You can also drag a vibe slider to blend between them.
      </>
    ),
  },
  {
    q: 'Do I need a Spotify Premium account?',
    a: (
      <>
        No. VibeScape works with a free Spotify account for library import, and plays audio previews
        or YouTube videos of your tracks. Spotify Premium unlocks in-app streaming of full songs via
        the Spotify Web Playback SDK.
      </>
    ),
  },
  {
    q: 'Can I try VibeScape without signing up?',
    a: (
      <>
        Yes. Click <em>Just listen</em> on the landing page to enter a shared demo library
        instantly, no account required. When you’re ready, sign in with Spotify to import your own
        music — your Spotify account is your VibeScape identity, no separate password or PIN.
      </>
    ),
  },
  {
    q: 'Is VibeScape free?',
    a: (
      <>
        Yes. Free, open source, no ads, no paid tiers, no data sold. Source lives on{' '}
        <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
          GitHub
        </a>
        .
      </>
    ),
  },
  {
    q: 'Does it work on my phone?',
    a: (
      <>
        Yes. The mobile layout uses your album art as a full-viewport backdrop, a slide-up queue
        sheet, and native Bluetooth transport controls through the Media Session API — so play,
        pause, and skip work from your headphones or car.
      </>
    ),
  },
  {
    q: 'What languages does VibeScape support?',
    a: (
      <>
        The library is language-agnostic. Whisper detects the sung language of each track —
        including Indian, Punjabi, Tamil, and other regional catalogs that western music APIs often
        miss — and labels them in the player. Instrumentals stay untagged rather than guessed.
      </>
    ),
  },
  {
    q: 'Where does the mood data come from?',
    a: (
      <>
        A fine-tuned MERT-95M transformer scores each track for danceability, energy, and valence
        directly from the audio. When Spotify’s own audio-features API was deprecated in 2024,
        VibeScape kept working — because it never depended on it.
      </>
    ),
  },
  {
    q: 'Can I run it on my own server?',
    a: (
      <>
        Yes. It’s a FastAPI + SQLite app with a static frontend. The heavy inference runs on a free
        Modal GPU tier, and the playback API fits in a 512&nbsp;MB VM. See the README for the full
        self-host guide.
      </>
    ),
  },
];

function Faq() {
  return (
    <section id="faq" className={styles.faq}>
      <SectionHead eyebrow="faq" tight>
        Questions we get a lot.
      </SectionHead>

      <div className={styles.list}>
        {ITEMS.map(({ q, a }) => (
          <details className={styles.item} key={q}>
            <summary>{q}</summary>
            <p>{a}</p>
          </details>
        ))}
      </div>
    </section>
  );
}

export default memo(Faq);
