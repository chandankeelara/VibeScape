import { Component, useEffect } from 'react';
import { usePlayer } from '../../../state/PlayerContext';
import { onMascot } from '../../../lib/mascotBus';
import BitRig from './BitRig';
import useBitState from './useBitState';
import { ANIMATIONS, META, classFor } from './manifest';
import styles from './BitDock.module.css';

/**
 * Bit, the full rig, standing beside the DJ line (DjLine.jsx).
 *
 * Before the S2 redesign he had a fixed strip along the bottom of the stage.
 * S2 has no such floor — the player bar owns the bottom edge and the queue
 * owns the right — so he lives where he has a job: next to the sentence the
 * DJ says, which is the one place in every layout that is about the DJ.
 * DjLine owns the slot's SIZE (it reserves the box while this chunk loads);
 * this component fills it.
 *
 * Loaded lazily by DjLine: the animation stylesheet is ~105KB (15KB gzipped) and nothing on
 * first paint needs it.
 *
 * A LEAF that reads the player context itself. usePlaybackTime() ticks ~4x a
 * second; nothing here subscribes to it, and the context value does not
 * carry the playhead, so playback never re-renders Bit. His motion is CSS —
 * React only picks a class when the state changes.
 *
 * `paused` freezes him in place without unmounting: the phone keeps the
 * full-screen player mounted (hidden) and the home list under it, and only
 * the one in view should be dancing. Unmounting would lose the picker's
 * memory and re-cue a record on every open.
 */
function BitDockInner({ paused }) {
  const { current, playing, loadingTrack, verifying, mode, source, vibe } = usePlayer();

  const { state, animKey, prop, beat, fire } = useBitState({
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
    <span
      className={`${styles.dock} ${paused ? styles.paused : ''}`}
      style={{ '--beat': `${beat}s` }}
      data-state={state}
      // Purely decorative: every state it reflects is already announced by a
      // real control or by the DJ line's aria-live sentence beside it.
      aria-hidden="true"
    >
      <BitRig className={`${styles.rig} ${classFor(animKey)}`} prop={prop} />
    </span>
  );
}

/**
 * Dev-only integrity check on the seam between the manifest and the CSS.
 *
 * ANIMATIONS, META and the stylesheet are three lists that must agree. A key
 * missing from any one of them fails SILENTLY — classFor() returns '', Bit
 * simply stands still, and nothing is logged.
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
 * rejected it. Firing celebrate would overstate it and firing sulk would be
 * wrong, so Bit stays in his base state and says nothing.
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
 * He is decoration, but a crash in here unmounts the whole tree above it —
 * which is what happened the first time he was wired up: one undefined
 * variable white-screened the app. The boundary is the guarantee.
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

export default function BitDock({ paused = false }) {
  return (
    <BitBoundary>
      <BitDockInner paused={paused} />
    </BitBoundary>
  );
}
