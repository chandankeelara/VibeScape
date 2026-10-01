import { useId } from 'react';
import styles from './DjSearching.module.css';

/**
 * Shown over the album art while the next track is being chosen and nothing
 * is loaded yet.
 *
 * The gap it covers is real: with DJ mode on, an empty queue means a round
 * trip to POST /similar before anything can play. Without this the hero card
 * sits on the previous track's art with no sign that work is happening,
 * which reads as a dead button.
 *
 * Inline SVG rather than a Lottie/GIF because it has to re-tint with the mood
 * accent — applyAccent() rewrites --vibe-accent on every track change, and a
 * raster asset can't follow that. It also costs nothing to fetch, on a
 * surface that only appears when the network is already busy.
 *
 * Paint order is load-bearing, see the section comments: the deck covers the
 * robot's waist, and the scratching arm is drawn last so the hand lands ON
 * the record instead of behind the deck.
 *
 * Note positions live in CSS, not in transform attributes: a CSS transform
 * overrides the attribute outright, so a note animated from CSS would snap to
 * the origin on the first frame.
 *
 * All motion is gated behind prefers-reduced-motion — see the module CSS.
 */
export default function DjSearching({ label = 'Finding your next track' }) {
  // SVG refs are document-global. A hardcoded id would collide the moment a
  // second instance mounted, and both visors would clip to whichever node
  // rendered last. The colons React puts in useId() are legal in an id but
  // not in a CSS identifier, so they are stripped before going into url(#…).
  const visorClip = `djVisor${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;

  return (
    <div className={styles.overlay} role="status" aria-live="polite">
      <svg
        className={styles.scene}
        viewBox="0 0 200 150"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        {/* Accent wash behind the robot, so it reads as lit by the deck. */}
        <ellipse className={styles.wash} cx="104" cy="92" rx="80" ry="48" />

        {/* ---------------------------------------------------- notes */}
        {/* Each note is drawn around its own origin and placed by CSS, which
            also carries the rise. Staggered delays keep the loop from
            reading as a single pulse. */}
        <g className={styles.note1}><Note /></g>
        <g className={styles.note2}><Note small /></g>
        <g className={styles.note3}><Note /></g>

        {/* ----------------------------------------------------- robot */}
        <g className={styles.robot}>
          {/* Raised arm first, so the shoulder joint is covered by the torso. */}
          <g className={styles.armRaise}>
            <Limb d="M154 74 L173 67 L180 47" />
            <Hand cx="180" cy="47" r="5" />
          </g>

          <rect className={styles.torso} x="94" y="62" width="62" height="48" rx="15" />
          <circle className={styles.chestLight} cx="125" cy="81" r="5.5" />
          <rect className={styles.neck} x="117" y="52" width="16" height="12" rx="5" />

          {/* The head bobs; the headphones and antenna ride with it. */}
          <g className={styles.head}>
            <rect className={styles.skull} x="101" y="17" width="48" height="37" rx="13" />
            <rect className={styles.visor} x="108" y="26" width="34" height="18" rx="9" />

            {/* Sweeps across the visor — the "searching" tell. Clipped so it
                never slides out over the skull. */}
            <clipPath id={visorClip}>
              <rect x="108" y="26" width="34" height="18" rx="9" />
            </clipPath>
            <g clipPath={`url(#${visorClip})`}>
              <rect className={styles.scan} x="102" y="26" width="10" height="18" />
            </g>

            <circle className={styles.eye} cx="118" cy="35" r="3.6" />
            <circle className={styles.eye} cx="132" cy="35" r="3.6" />

            <path className={styles.band} d="M97 33 Q125 3 153 33" />
            <rect className={styles.cup} x="92" y="28" width="12" height="19" rx="6" />
            <rect className={styles.cup} x="146" y="28" width="12" height="19" rx="6" />

            <line className={styles.antenna} x1="125" y1="17" x2="125" y2="7" />
            <circle className={styles.bulb} cx="125" cy="5" r="4" />
          </g>
        </g>

        {/* ------------------------------------------------------ deck */}
        {/* After the robot, so it overlaps the waist and the robot reads as
            standing behind the booth. */}
        <rect className={styles.deck} x="10" y="104" width="180" height="35" rx="10" />

        <g className={styles.platter}>
          <circle className={styles.vinyl} cx="48" cy="121" r="19" />
          <circle className={styles.groove} cx="48" cy="121" r="14.5" />
          <circle className={styles.groove} cx="48" cy="121" r="10" />
          <circle className={styles.spindle} cx="48" cy="121" r="5.5" />
          {/* Off-centre nick: a perfect circle gives no sense of rotation, so
              without this the spin is invisible. */}
          <circle className={styles.nick} cx="48" cy="109" r="1.6" />
        </g>

        <circle className={styles.pivot} cx="86" cy="111" r="4" />
        <line className={styles.tonearm} x1="86" y1="111" x2="62" y2="118" />

        <g className={styles.eq}>
          <rect className={styles.bar1} x="118" y="115" width="7" height="19" rx="3.5" />
          <rect className={styles.bar2} x="130" y="115" width="7" height="19" rx="3.5" />
          <rect className={styles.bar3} x="142" y="115" width="7" height="19" rx="3.5" />
          <rect className={styles.bar4} x="154" y="115" width="7" height="19" rx="3.5" />
          <rect className={styles.bar5} x="166" y="115" width="7" height="19" rx="3.5" />
        </g>

        {/* ---------------------------------------------- scratching arm */}
        {/* LAST on purpose. Drawn before the deck it ends up behind it, and
            the hand disappears instead of resting on the record. The elbow
            kicks out sideways so the arm reads as jointed, not as a stick. */}
        <g className={styles.armScratch}>
          <Limb d="M97 76 L71 79 L50 112" />
          <Hand cx="50" cy="112" r="5.5" />
        </g>
      </svg>

      <p className={styles.caption}>
        {label}
        <span className={styles.dots} aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
      </p>
    </div>
  );
}

/**
 * A limb is two strokes, not one: a wide dark stroke underneath and a
 * narrower surface-coloured stroke on top. That gives the arm the same
 * outlined look as the torso, which a single flat stroke can't — SVG strokes
 * have no separate fill.
 */
function Limb({ d }) {
  return (
    <>
      <path className={styles.limbOutline} d={d} />
      <path className={styles.limbFill} d={d} />
    </>
  );
}

function Hand({ cx, cy, r }) {
  return <circle className={styles.hand} cx={cx} cy={cy} r={r} />;
}

/** Eighth note, drawn around (0,0) so CSS owns where it sits. */
function Note({ small = false }) {
  const s = small ? 0.82 : 1;
  return (
    <g transform={`scale(${s})`}>
      <path className={styles.noteStem} d="M2.4 0 V-11 L8 -12.8 V-1.8" />
      <circle className={styles.noteHead} cx="0" cy="0" r="2.6" />
      <circle className={styles.noteHead} cx="5.6" cy="-1.8" r="2.6" />
    </g>
  );
}
