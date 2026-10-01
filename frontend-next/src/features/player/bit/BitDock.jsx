import { Component, useEffect } from 'react';
import { usePlayer } from '../../../state/PlayerContext';
import { onMascot } from '../../../lib/mascotBus';
import BitRig from './BitRig';
import BitScene from './BitScene';
import useBitState from './useBitState';
import { ANIMATIONS, META, classFor } from './manifest';
import styles from './BitDock.module.css';

/**
 * Bit's home: a fixed strip across the bottom of the stage column.
 *
 * Fixed rather than a grid row because .shell is a rigid 100dvh grid
 * (PlayerPage.module.css) and adding a row would squeeze the hero card on
 * every viewport. The stage centres its content vertically, so this band sits
 * in space that is already empty.
 *
 * NEVER takes pointer events. The band spans the full stage width, so without
 * `pointer-events: none` it would swallow clicks on the transport and the
 * mood slider underneath it.
 *
 * Deliberately a LEAF that reads the player context directly instead of
 * taking props. usePlaybackTime() ticks ~4x/second; if Bit's state lived in
 * PlayerPage, every one of those ticks would re-render the whole stage.
 */
function BitDockInner() {
  const { current, playing, loadingTrack, verifying, mode, source, vibe } = usePlayer();

  const { state, animKey, prop, beat, station, fire } = useBitState({
    current,
    playing,
    loadingTrack,
    verifying,
    mode,
    source,
    vibe,
  });

  useReactions(fire);
  useManifestAudit();

  return (
    <div
      className={styles.dock}
      style={{ '--beat': `${beat}s` }}
      data-state={state}
      data-station={station}
      // Purely decorative: every state it reflects is already announced by a
      // real control or an aria-live region elsewhere in the player.
      aria-hidden="true"
    >
      <BitScene playing={playing} />

      <div className={styles.spot}>
        <BitRig className={`${styles.rig} ${classFor(animKey)}`} prop={prop} />
      </div>
    </div>
  );
}

/**
 * Dev-only integrity check on the seam between the manifest and the CSS.
 *
 * ANIMATIONS, META and the stylesheet are three lists that must agree. A key
 * missing from any one of them fails SILENTLY — classFor() returns '', Bit
 * simply stands still, and nothing is logged. That is the single most likely
 * bug in this whole feature, so it gets an explicit check.
 */
function useManifestAudit() {
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const problems = [];
    Object.entries(ANIMATIONS).forEach(([state, pool]) => {
      if (!pool || pool.length === 0) problems.push(`state "${state}" has no animations`);
      (pool || []).forEach((key) => {
        if (!classFor(key)) problems.push(`"${key}" (${state}) has no CSS class`);
        if (!META[key]) problems.push(`"${key}" (${state}) is missing from META`);
        else if (META[key].loop === false && !META[key].ms) {
          problems.push(`"${key}" is a one-shot with no ms — Bit will stick in it`);
        }
      });
    });
    if (problems.length) {
      console.warn(`[Bit] manifest issues:\n  ${problems.join('\n  ')}`);
    }
  }, []);
}

/**
 * DJ actions -> Bit's one-shot reactions.
 *
 * The mapping lives here rather than at the emit site because the queue has no
 * business knowing that a skip makes a robot sulk — it just reports what the
 * listener did.
 *
 * `next` is deliberately unmapped. classifyTransition() uses it for the
 * ambiguous 45-85% band, where the listener neither finished the track nor
 * rejected it; the weighting treats it as a weak positive. Firing celebrate
 * would overstate it and firing sulk would be wrong, so Bit stays in its base
 * state and says nothing.
 */
const REACTION = {
  completed: 'celebrate',
  skipped: 'sulk',
  queued: 'catch',
  rewind: 'rewind',
};

function useReactions(fire) {
  useEffect(() => onMascot((action) => {
    const state = REACTION[action];
    if (state) fire(state);
  }), [fire]);
}

/**
 * Bit must never be able to take the player down.
 *
 * He is decoration — nobody loses a feature if he vanishes, but a crash in
 * here unmounts the whole tree above it, and that is exactly what happened
 * the first time this was wired up: a single undefined variable white-screened
 * the entire app. A build cannot catch that in JS, so the boundary is the
 * guarantee rather than the testing.
 */
class BitBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { dead: false };
  }

  static getDerivedStateFromError() {
    return { dead: true };
  }

  componentDidCatch(error) {
    // Loud in dev, silent in prod — the user cannot act on a mascot crash.
    if (import.meta.env.DEV) console.error('[Bit] crashed, unmounting:', error);
  }

  render() {
    return this.state.dead ? null : this.props.children;
  }
}

export default function BitDock() {
  return (
    <BitBoundary>
      <BitDockInner />
    </BitBoundary>
  );
}
