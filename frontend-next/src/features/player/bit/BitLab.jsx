import { useEffect, useState } from 'react';
import BitRig from './BitRig';
import BitScene from './BitScene';
import { ANIMATIONS, META, SUGGESTED_PROP, classFor } from './manifest';
import styles from './BitLab.module.css';

/**
 * Review page for every one of Bit's animations. Reached at /next/?bit
 *
 * Exists because the rig, the keyframes and the manifest are three separate
 * lists that must agree, and a mismatch fails SILENTLY — classFor() returns ''
 * and Bit simply stands still. Rendering every key side by side is the only
 * way to see that, and this page needs no auth, no PlayerProvider and no
 * playing track, so it can run before the mascot is wired into anything.
 *
 * The BPM slider drives --beat exactly as the dock does, so the groove row
 * can be checked against a real tempo range rather than a guess.
 */
export default function BitLab() {
  const [bpm, setBpm] = useState(120);
  const [only, setOnly] = useState('all');
  const [slow, setSlow] = useState(false);

  // Mirrors useBitState.beatSeconds: fast songs fold onto the 2 and 4.
  let beat = 60 / bpm;
  while (beat < 0.42) beat *= 2;
  while (beat > 1.4) beat /= 2;

  const states = Object.keys(ANIMATIONS).filter((s) => only === 'all' || s === only);
  const total = Object.values(ANIMATIONS).reduce((n, p) => n + p.length, 0);

  return (
    <div
      className={`${styles.page} ${slow ? styles.slow : ''}`}
      style={{ '--beat': `${beat}s` }}
    >
      <header className={styles.bar}>
        <strong className={styles.brand}>Bit · {total} animations</strong>

        <label className={styles.ctl}>
          <span>bpm</span>
          <input
            type="range" min="45" max="235" value={bpm}
            onChange={(e) => setBpm(Number(e.target.value))}
          />
          <code>{bpm}</code>
          <code>beat {beat.toFixed(2)}s</code>
        </label>

        <select className={styles.sel} value={only} onChange={(e) => setOnly(e.target.value)}>
          <option value="all">all states</option>
          {Object.keys(ANIMATIONS).map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>

        <label className={styles.check}>
          <input type="checkbox" checked={slow} onChange={(e) => setSlow(e.target.checked)} />
          <span>¼ speed</span>
        </label>

        <span className={styles.swatches}>
          {[['#7c3aed', '#a78bfa'], ['#22c1e3', '#7fe3f5'], ['#1ed760', '#7df0a8'],
            ['#ff5d5d', '#ffa0a0'], ['#f5a524', '#ffd08a']].map(([a, b]) => (
              <button
                key={a} type="button" style={{ background: a }} aria-label={`accent ${a}`}
                onClick={() => {
                  document.documentElement.style.setProperty('--vibe-accent', a);
                  document.documentElement.style.setProperty('--vibe-accent-2', b);
                }}
              />
            ))}
        </span>
      </header>

      {/* The room, at the width it actually ships at. Shown first because
          the furniture is what sets the scale Bit is drawn against. */}
      <section className={styles.group}>
        <h2 className={styles.h}>
          the room
          <span className={styles.meta}>isometric | set dressing | beat-reactive</span>
        </h2>
        <div className={styles.rooms}>
          <Room label="playing" playing />
          <Room label="stopped" />
          <Room label="using the crate - library stands down" playing using="crate" />
          <Room label="using the deck - booth stands down" playing using="deck" />
        </div>
      </section>

      {states.map((state) => (
        <section key={state} className={styles.group}>
          <h2 className={styles.h}>
            {state}
            <span className={styles.meta}>
              {ANIMATIONS[state].length} variant{ANIMATIONS[state].length === 1 ? '' : 's'}
              {' · '}
              {SUGGESTED_PROP[state] ? `prop: ${SUGGESTED_PROP[state]}` : 'no prop'}
            </span>
          </h2>

          <div className={styles.row}>
            {ANIMATIONS[state].map((key) => (
              <Cell key={key} animKey={key} state={state} slow={slow} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

/**
 * One animation cell.
 *
 * One-shots are REPLAYED here on a timer. In the player a one-shot firing once
 * and handing back to the base state is exactly right, but on a review page it
 * means the clip runs at mount and then Bit stands frozen forever — which
 * reads as "celebrate has no animation" when the keyframes are fine.
 *
 * Remounting via `key` is what restarts it: re-applying the same class to a
 * live element does nothing, because the animation has already finished on it.
 */
function Cell({ animKey, state, slow }) {
  const cls = classFor(animKey);
  const m = META[animKey];
  // A missing class is the silent failure this page exists to surface, so it
  // is called out rather than looking like a calm idle pose.
  const bad = !cls || !m;
  const oneShot = !!m && m.loop === false;

  const [run, setRun] = useState(0);

  useEffect(() => {
    if (!oneShot) return undefined;
    // Slow mode overrides --t to a flat 4s, so the authored ms no longer
    // describes how long the clip takes and replaying on it would cut it off.
    const clip = slow ? 4000 : m.ms || 800;
    const id = setInterval(() => setRun((n) => n + 1), clip + 700); // + a beat of rest
    return () => clearInterval(id);
  }, [oneShot, m, slow]);

  return (
    <figure className={styles.cell}>
      <div className={`${styles.stage} ${bad ? styles.broken : ''}`}>
        <BitRig
          key={run}
          className={`${styles.rig} ${cls}`}
          prop={SUGGESTED_PROP[state] || null}
        />
      </div>
      <figcaption className={styles.cap}>
        <span className={styles.key}>{animKey.replace(/^a_/, '')}</span>
        <span className={styles.tag}>
          {!cls ? 'NO CSS' : !m ? 'NO META' : m.loop ? 'loop' : `${m.ms}ms`}
        </span>
      </figcaption>
    </figure>
  );
}

/**
 * One preview of the set dressing at a realistic width.
 *
 * Mirrors the dock's own box - a wide, short strip with the furniture on the
 * floor and empty headroom above - so the proportions here match what ships.
 * Previewing the scene in a square would flatter it and hide exactly the
 * problems worth catching.
 */
function Room({ label, playing = false, using = null }) {
  return (
    <figure className={styles.roomCell}>
      <div className={styles.roomStage}>
        <BitScene playing={playing} using={using} />
      </div>
      <figcaption className={styles.cap}>
        <span className={styles.key}>{label}</span>
      </figcaption>
    </figure>
  );
}
