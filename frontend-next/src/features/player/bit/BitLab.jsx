import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import BitRig from './BitRig';
import BitScene from './BitScene';
import { STATION } from './useBitState';
import { ANIMATIONS, META, SUGGESTED_PROP, classFor } from './manifest';
import dock from './BitDock.module.css';
import styles from './BitLab.module.css';

/**
 * Bit's review page. Reached at /next/?bit
 *
 * Three jobs, in the order they matter:
 *
 *   1. IN CONTEXT — Bit at the exact size and position he occupies in the
 *      player, so "does this earn its place?" can actually be answered. A
 *      grid of isolated cells flatters him: it shows the artwork, not the
 *      experience.
 *   2. THE ROUTINE — the real sequence (dig, cue, dance) end to end at real
 *      timings, because each beat is only as good as its hand-off.
 *   3. EVERY VARIANT — the full grid, for choosing between options.
 *
 * The in-context stage imports the REAL BitDock.module.css rather than
 * approximating it. CSS Modules hash per file, so reusing the module yields
 * the identical hashed classes and this preview cannot drift from what ships.
 * Only `position` is overridden, since the dock is fixed to the viewport and
 * here it has to sit in the page.
 */

const STATES = Object.keys(ANIMATIONS);

/**
 * Every prop kind, with the state that shows it off best.
 *
 * Props are only ever drawn in Bit's hands, so showing them disembodied would
 * judge the wrong thing — what matters is how one reads at arm's length in a
 * real pose. Each entry picks the variant where the prop does the most work.
 */
const PROPS = [
  { prop: 'crate', state: 'digging', note: 'stands in for the library, which hides while he holds it' },
  { prop: 'record', state: 'celebrate', note: 'the most-seen prop - celebrate, catch, sulk' },
  { prop: 'deck', state: 'cueing', note: 'platter, tonearm and start button all live on it' },
  { prop: 'rewind', state: 'rewind', note: 'a disc spinning backwards is ambiguous, so the badge states it' },
  { prop: 'glasses', state: 'watching', note: 'worn on the visor in video mode' },
  { prop: 'padlock', state: 'locked', note: 'Spotify-only track, nothing to play' },
  { prop: 'question', state: 'confused', note: 'a symbol, deliberately flat' },
  { prop: 'zzz', state: 'asleep', note: 'a symbol, deliberately flat' },
];

/** Where a one-shot lands when the routine finishes, as in the dock. */
const BASE_FALLBACK = 'groove';

export default function BitLab() {
  const [bpm, setBpm] = useState(120);
  const [speed, setSpeed] = useState(1);
  const [state, setState] = useState('groove');
  const [variant, setVariant] = useState(0);
  const [only, setOnly] = useState('all');

  // Mirrors useBitState.beatSeconds: fast songs fold onto the 2 and 4.
  const beat = useMemo(() => {
    let b = 60 / bpm;
    while (b < 0.42) b *= 2;
    while (b > 1.4) b /= 2;
    return b * speed;
  }, [bpm, speed]);

  const pool = ANIMATIONS[state] || [];
  const animKey = pool[Math.min(variant, pool.length - 1)];
  const prop = SUGGESTED_PROP[state] || null;
  const station = STATION[state] || 'floor';

  const pick = useCallback((s, v = 0) => { setState(s); setVariant(v); }, []);

  const total = STATES.reduce((n, s) => n + ANIMATIONS[s].length, 0);
  const shown = only === 'all' ? STATES : STATES.filter((s) => s === only);

  return (
    <div className={styles.page} style={{ '--beat': `${beat}s` }}>
      <TopBar total={total} bpm={bpm} setBpm={setBpm} speed={speed} setSpeed={setSpeed} />

      {/* ----------------------------------------------------- in context */}
      <section className={styles.block}>
        <Head
          n="01"
          title="In the player"
          note="Real dock stylesheet, real scale, real station. Nothing here is a mock-up."
        />

        <div className={styles.stageWrap}>
          <div className={`${dock.dock} ${styles.stageDock}`} data-station={station}>
            <BitScene playing={state === 'groove'} using={prop} />
            <div className={dock.spot}>
              <BitRig className={`${dock.rig} ${classFor(animKey)}`} prop={prop} />
            </div>
          </div>
          <Routine onStep={pick} />
        </div>

        <div className={styles.picker}>
          {STATES.map((s) => (
            <button
              key={s}
              type="button"
              className={s === state ? styles.chipOn : styles.chip}
              onClick={() => pick(s)}
            >
              {s}<em>{ANIMATIONS[s].length}</em>
            </button>
          ))}
        </div>

        <div className={styles.variants}>
          <span className={styles.vLabel}>variant</span>
          {pool.map((k, i) => (
            <button
              key={k}
              type="button"
              className={i === variant ? styles.vOn : styles.v}
              onClick={() => setVariant(i)}
            >
              {k.replace(/^a_\w+?_/, '')}
            </button>
          ))}
          <span className={styles.vMeta}>
            {META[animKey] && META[animKey].loop ? 'loops' : `${META[animKey] && META[animKey].ms}ms one-shot`}
            {prop ? ` · holds the ${prop}` : ' · no prop'}
            {` · at the ${station}`}
          </span>
        </div>
      </section>

      {/* --------------------------------------------------------- the room */}
      <section className={styles.block}>
        <Head
          n="02"
          title="The room"
          note="Isometric set dressing. Depth runs on one shared vector, which is what makes it a space rather than three unrelated props."
        />
        <div className={styles.rooms}>
          <Room label="playing" playing />
          <Room label="stopped" />
          <Room label="Bit has the crate — library stands down" playing using="crate" />
          <Room label="Bit has the deck — booth stands down" playing using="deck" />
        </div>
      </section>

      {/* ------------------------------------------------------------ props */}
      <section className={styles.block}>
        <Head
          n="03"
          title="The props"
          note="All eight, each in the pose that shows it off, at double the grid size. Props are only ever seen in Bit's hands, so they are judged there rather than laid out on their own."
        />
        <div className={styles.props}>
          {PROPS.map((p) => (
            <PropCell key={p.prop} {...p} speed={speed} onPick={pick} />
          ))}
        </div>
      </section>

      {/* --------------------------------------------------- every variant */}
      <section className={styles.block}>
        <Head
          n="04"
          title="Every animation"
          note={`${total} clips across ${STATES.length} states. One-shots replay on a timer here; in the player they fire once and hand back. Click any cell to load it into the preview above.`}
        >
          <select className={styles.sel} value={only} onChange={(e) => setOnly(e.target.value)}>
            <option value="all">all states</option>
            {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </Head>

        {shown.map((s) => (
          <div key={s} className={styles.group}>
            <h3 className={styles.groupH}>
              {s}
              <span className={styles.groupMeta}>
                {SUGGESTED_PROP[s] ? `prop: ${SUGGESTED_PROP[s]}` : 'no prop'}
                {` · at the ${STATION[s] || 'floor'}`}
                {s === 'groove' ? ' · chosen by mood band, not at random' : ''}
              </span>
            </h3>
            <div className={styles.row}>
              {ANIMATIONS[s].map((key, i) => (
                <Cell
                  key={key}
                  animKey={key}
                  state={s}
                  speed={speed}
                  onPick={() => pick(s, i)}
                />
              ))}
            </div>
          </div>
        ))}
      </section>
    </div>
  );
}

/* ----------------------------------------------------------------- chrome */

function TopBar({ total, bpm, setBpm, speed, setSpeed }) {
  const reduced =
    typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

  return (
    <header className={styles.bar}>
      <span className={styles.brand}>Bit<em>{total} animations</em></span>

      <label className={styles.ctl}>
        <span>tempo</span>
        <input
          type="range" min="45" max="235" value={bpm}
          onChange={(e) => setBpm(Number(e.target.value))}
        />
        <code>{bpm} bpm</code>
      </label>

      <span className={styles.seg}>
        {[1, 2, 4].map((s) => (
          <button
            key={s} type="button"
            className={speed === s ? styles.segOn : styles.segBtn}
            onClick={() => setSpeed(s)}
          >
            {s === 1 ? 'full' : `1/${s}`}
          </button>
        ))}
      </span>

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

      {/*
       * Called out rather than silently ignored. With reduce-motion on, every
       * animation on this page is dead by CSS and Bit holds a resting pose —
       * without this banner the page looks broken and the mascot looks static.
       */}
      {reduced && <span className={styles.warn}>reduce-motion is ON — nothing will animate</span>}
    </header>
  );
}

function Head({ n, title, note, children }) {
  return (
    <div className={styles.head}>
      <span className={styles.headN}>{n}</span>
      <div className={styles.headText}>
        <h2 className={styles.headTitle}>{title}</h2>
        <p className={styles.headNote}>{note}</p>
      </div>
      {children}
    </div>
  );
}

/* ---------------------------------------------------------------- routine */

/**
 * Plays the real sequence at real timings: dig a record out, start it, dance.
 *
 * The hand-offs are the point. Each clip looks fine alone; what decides
 * whether the mascot works is whether the end of one pose flows into the
 * start of the next, and that is exactly what a grid cannot show.
 */
function Routine({ onStep }) {
  const [running, setRunning] = useState(false);
  const timers = useRef([]);

  const clear = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }, []);

  useEffect(() => clear, [clear]);

  const play = () => {
    clear();
    setRunning(true);
    const cue = ANIMATIONS.cueing ? ANIMATIONS.cueing[0] : null;
    const cueMs = (cue && META[cue] && META[cue].ms) || 900;

    onStep('digging');
    timers.current.push(setTimeout(() => onStep('cueing'), 2200));
    timers.current.push(setTimeout(() => onStep(BASE_FALLBACK), 2200 + cueMs));
    timers.current.push(setTimeout(() => setRunning(false), 2200 + cueMs + 2600));
  };

  return (
    <button type="button" className={styles.routine} onClick={play} disabled={running}>
      <span>{running ? 'playing…' : 'play the routine'}</span>
      <em>dig · cue · dance</em>
    </button>
  );
}

/* ------------------------------------------------------------------ cells */

function Room({ label, playing = false, using = null }) {
  return (
    <figure className={styles.roomCell}>
      <div className={styles.roomStage}>
        <BitScene playing={playing} using={using} />
      </div>
      <figcaption className={styles.cap}>{label}</figcaption>
    </figure>
  );
}

/**
 * One animation cell.
 *
 * One-shots are REPLAYED on a timer. In the player, firing once and handing
 * back is exactly right; on a review page it means the clip runs at mount and
 * then Bit stands frozen forever, which reads as "this animation is missing"
 * when the keyframes are fine.
 *
 * Remounting via `key` is what restarts it — re-applying the same class to a
 * live element does nothing, because the animation has already finished on it.
 */
function Cell({ animKey, state, speed, onPick }) {
  const cls = classFor(animKey);
  const m = META[animKey];
  const bad = !cls || !m;
  const oneShot = !!m && m.loop === false;
  const [run, setRun] = useState(0);

  useEffect(() => {
    if (!oneShot) return undefined;
    const clip = (m.ms || 800) * speed;
    const id = setInterval(() => setRun((n) => n + 1), clip + 650);
    return () => clearInterval(id);
  }, [oneShot, m, speed]);

  return (
    <figure className={styles.cell}>
      <button
        type="button"
        className={`${styles.cellStage} ${bad ? styles.broken : ''}`}
        onClick={onPick}
        title="Load this one into the preview above"
      >
        <BitRig
          key={run}
          className={`${styles.cellRig} ${cls}`}
          prop={SUGGESTED_PROP[state] || null}
        />
      </button>
      <figcaption className={styles.cap}>
        <span className={styles.cellKey}>{animKey.replace(/^a_\w+?_/, '')}</span>
        <span className={styles.cellTag}>
          {!cls ? 'NO CSS' : !m ? 'NO META' : m.loop ? 'loop' : `${m.ms}ms`}
        </span>
      </figcaption>
    </figure>
  );
}

/**
 * One prop, shown in Bit's hands at double the grid size.
 *
 * Reuses Cell's replay trick: one-shots fire once and then sit frozen, which
 * on a review page reads as a missing animation rather than a finished one.
 */
function PropCell({ prop, state, note, speed, onPick }) {
  const animKey = (ANIMATIONS[state] || [])[0];
  const m = META[animKey];
  const oneShot = !!m && m.loop === false;
  const [run, setRun] = useState(0);

  useEffect(() => {
    if (!oneShot) return undefined;
    const clip = (m.ms || 800) * speed;
    const id = setInterval(() => setRun((n) => n + 1), clip + 650);
    return () => clearInterval(id);
  }, [oneShot, m, speed]);

  return (
    <figure className={styles.propCell}>
      <button
        type="button"
        className={styles.propStage}
        onClick={() => onPick(state, 0)}
        title="Load this state into the preview above"
      >
        <BitRig key={run} className={`${styles.propRig} ${classFor(animKey)}`} prop={prop} />
      </button>
      <figcaption className={styles.propCap}>
        <span className={styles.propName}>{prop}</span>
        <span className={styles.propNote}>{note}</span>
      </figcaption>
    </figure>
  );
}
