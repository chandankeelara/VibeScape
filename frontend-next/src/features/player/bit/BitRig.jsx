import styles from './animations.module.css';

/**
 * Bit — the DJ-robot mascot rig.
 *
 * This file is GEOMETRY ONLY. It has no state, no effects and no timers: every
 * movement Bit makes comes from a single animation class the dock puts on the
 * root `<svg>`, which drives the parts below through descendant selectors. That
 * split is deliberate — a mascot that needed JS to animate would re-render the
 * player tree on every frame, and the media layer already owns the one 60fps
 * loop this app is allowed to have (the AnalyserNode glow).
 *
 * Visual language is inherited from DjSearching: surface-2 bodies outlined in
 * border-strong, a dark visor, accent eyes. Two traps carried over from there,
 * both documented at length in animations.module.css:
 *
 *   1. A CSS transform OVERRIDES an SVG `transform` attribute outright. So no
 *      element that CSS animates may carry a transform attribute — it would be
 *      silently discarded on the first frame and the part would snap to the
 *      viewBox origin. The only transform attributes in this file are on inert
 *      wrapper `<g>`s that no selector targets (the prop placement groups).
 *   2. Rotation needs `transform-box: view-box` so `transform-origin` resolves
 *      in viewBox user units — the same numbers as the coordinates here. The
 *      joint origins (shoulders at y=58, hips at y=84, neck at 50,48) are set
 *      in the CSS and must stay in step with this markup.
 *
 * Sized to read at 84-128px tall. At that size detail below ~2 user units
 * disappears, which is why nothing here is finer than that.
 *
 * Every animatable part is a `<g>` with a stable class name AND a matching
 * `data-part`, so a part can be found in devtools without decoding the hashed
 * CSS-module class.
 */
export default function BitRig({ className, prop = null }) {
  return (
    <svg
      className={className}
      // Stable hook the reduced-motion block uses to kill every animation in
      // one rule, instead of enumerating 34 state classes and their descendants.
      data-bit=""
      viewBox="0 0 100 120"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      focusable="false"
    >
      {/* Contact shadow. Squashing it is what sells a jump — without a shadow
          that shrinks, a rig translating up reads as "the camera moved". */}
      <g className={styles.shadow} data-part="shadow">
        <ellipse cx="50" cy="114" rx="24" ry="4" />
      </g>

      <g className={styles.body} data-part="body">
        {/* Far arm first so its shoulder joint is covered by the torso. */}
        <g className={styles.armL} data-part="armL">
          <Limb d="M31 58 L22 69 L25 81" />
          <circle className={styles.hand} cx="25" cy="81" r="4.6" />
        </g>

        {/* Legs before the torso for the same reason: hips tuck under. */}
        <g className={styles.legL} data-part="legL">
          <Limb d="M41 83 L39 96 L40 107" />
          <ellipse className={styles.foot} cx="38" cy="109" rx="7" ry="4" />
        </g>
        <g className={styles.legR} data-part="legR">
          <Limb d="M59 83 L61 96 L60 107" />
          <ellipse className={styles.foot} cx="62" cy="109" rx="7" ry="4" />
        </g>

        <g className={styles.torso} data-part="torso">
          {/* Neck is drawn before the shell so the shell's outline closes over
              it; it is not an animatable part — the head owns that motion. */}
          <rect className={styles.neck} x="44" y="43" width="12" height="12" rx="5" />
          <rect className={styles.shell} x="31" y="51" width="38" height="33" rx="12" />
          <circle className={styles.chest} cx="50" cy="65" r="4.6" />
        </g>

        {/* Head group: everything on it must ride with it, so visor, eyes,
            antenna, headphones and band are all children. */}
        <g className={styles.head} data-part="head">
          <rect className={styles.skull} x="28" y="12" width="44" height="34" rx="12" />

          <g className={styles.visor} data-part="visor">
            <rect x="34" y="20" width="32" height="17" rx="8" />
          </g>

          <g className={styles.eyeL} data-part="eyeL">
            <circle cx="42" cy="28.5" r="3.4" />
          </g>
          <g className={styles.eyeR} data-part="eyeR">
            <circle cx="58" cy="28.5" r="3.4" />
          </g>

          <g className={styles.band} data-part="band">
            <path d="M26 29 Q50 3 74 29" />
          </g>
          <g className={styles.cupL} data-part="cupL">
            <rect x="21" y="22" width="11" height="17" rx="5.5" />
          </g>
          <g className={styles.cupR} data-part="cupR">
            <rect x="68" y="22" width="11" height="17" rx="5.5" />
          </g>

          <g className={styles.antenna} data-part="antenna">
            <line x1="50" y1="12" x2="50" y2="5" />
          </g>
          <g className={styles.bulb} data-part="bulb">
            <circle cx="50" cy="3" r="3.2" />
          </g>
        </g>

        {/* Near arm after the torso and head so a held prop lands in front of
            the body rather than behind it. */}
        <g className={styles.armR} data-part="armR">
          <Limb d="M69 58 L78 69 L75 81" />
          <circle className={styles.hand} cx="75" cy="81" r="4.6" />
        </g>

        {/* Prop slot. Always rendered, even when empty, so the part exists for
            selectors and the DOM shape does not change between states.

            Each accessory is drawn around (0,0) and placed by a transform
            ATTRIBUTE on an inert inner <g> — see trap 1 above. The outer .prop
            group is left at identity so CSS owns it completely; its pivot comes
            from a per-prop `transform-origin` keyed off data-prop in the CSS,
            which is why that attribute is load-bearing and not just debug aid. */}
        <g className={styles.prop} data-part="prop" data-prop={prop || 'none'}>
          <Prop kind={prop} />
        </g>
      </g>
    </svg>
  );
}

/**
 * A limb is two stacked strokes — a wide dark one, then a narrower surface one
 * on top. SVG strokes have no separate fill, so this is the only way to give an
 * arm the same outlined silhouette as the torso. Same technique as DjSearching.
 */
function Limb({ d }) {
  return (
    <>
      <path className={styles.limbOutline} d={d} />
      <path className={styles.limbFill} d={d} />
    </>
  );
}

/**
 * Props are intentionally crude. At 84-128px tall the whole robot is ~110px, so
 * an accessory gets maybe 20px of screen — it has to read as a silhouette, and
 * anything with interior detail turns to mush.
 */
function Prop({ kind }) {
  switch (kind) {
    case 'record':
      // Held out at arm's length on the right. The off-centre nick is what
      // makes a spin visible; a plain disc rotating looks motionless.
      //
      // The burst is an impact flash — a ring plus six spokes, parked at
      // opacity 0. It is the cheapest way to turn "the record is near the
      // hand" into "the record ARRIVED", which is the whole readability
      // complaint about catch and celebrate. Only those states switch it on.
      return (
        <g transform="translate(78 74)">
          <g className={styles.burst}>
            <circle className={styles.burstRing} cx="0" cy="0" r="13" />
            <g className={styles.burstSpokes}>
              <line x1="0" y1="-15" x2="0" y2="-20" />
              <line x1="13" y1="-7.5" x2="17.3" y2="-10" />
              <line x1="13" y1="7.5" x2="17.3" y2="10" />
              <line x1="0" y1="15" x2="0" y2="20" />
              <line x1="-13" y1="7.5" x2="-17.3" y2="10" />
              <line x1="-13" y1="-7.5" x2="-17.3" y2="-10" />
            </g>
          </g>
          <circle className={styles.vinyl} cx="0" cy="0" r="11" />
          <circle className={styles.groove} cx="0" cy="0" r="8" />
          <circle className={styles.groove} cx="0" cy="0" r="5.5" />
          <circle className={styles.label} cx="0" cy="0" r="3.2" />
          <circle className={styles.nick} cx="0" cy="-7" r="1.2" />
        </g>
      );

    case 'crate':
      // Sits on the floor in front of the feet. The spines are what say
      // "records", so they stay even though they are only 1.6 units apart.
      //
      // The two loose discs are drawn BEFORE the crate front so they emerge
      // from inside it rather than sliding over it. An arm waving above a
      // static box does not read as digging; records actually coming out of
      // the box does. Parked at opacity 0 — only `digging` ejects them.
      //
      // Parked at y=99, not on the floor at y=103. Bit's arm is ~24 user units
      // from shoulder to hand and the lowest the hand can physically reach is
      // y≈82, so a crate sitting on the floor is simply out of range — the arm
      // waved in the air above a box it could never touch. Four units up, plus
      // the body dip the digging animations add, puts the hand at the rim.
      return (
        <g transform="translate(50 99)">
          <g className={styles.crateDiscA}>
            <circle className={styles.vinyl} cx="0" cy="0" r="8.5" />
            <circle className={styles.groove} cx="0" cy="0" r="5.5" />
            <circle className={styles.label} cx="0" cy="0" r="2.6" />
          </g>
          <g className={styles.crateDiscB}>
            <circle className={styles.vinyl} cx="0" cy="0" r="7.5" />
            <circle className={styles.label} cx="0" cy="0" r="2.4" />
          </g>
          <rect className={styles.crate} x="-19" y="-10" width="38" height="20" rx="3" />
          <g className={styles.spines}>
            <line x1="-13" y1="-7" x2="-13" y2="7" />
            <line x1="-8" y1="-7" x2="-8" y2="7" />
            <line x1="-3" y1="-7" x2="-3" y2="7" />
            <line x1="2" y1="-7" x2="2" y2="7" />
            <line x1="7" y1="-7" x2="7" y2="7" />
          </g>
        </g>
      );

    case 'deck':
      // The whole DJ set, on the floor in front of the feet. Added for `sulk`:
      // a rejected track should cost Bit something visible, and there was
      // nothing in the prop vocabulary big enough to throw.
      //
      // Split into `deckBase` and `deckDisc` so the platter can leave without
      // the plinth — one variant hurls just the record, another flips the
      // entire rig. One prop, three different kinds of destruction.
      return (
        <g transform="translate(50 100)">
          <g className={styles.deckBase}>
            <rect className={styles.crate} x="-25" y="-9" width="50" height="18" rx="3" />
            <circle className={styles.pivot} cx="14" cy="-4" r="2.4" />
            <line className={styles.tonearm} x1="14" y1="-4" x2="2" y2="0" />
            <circle className={styles.knob} cx="15" cy="4" r="2.2" />
            <circle className={styles.knob} cx="21" cy="4" r="2.2" />
          </g>
          <g className={styles.deckDisc}>
            <circle className={styles.vinyl} cx="-8" cy="-1" r="8.5" />
            <circle className={styles.groove} cx="-8" cy="-1" r="5.5" />
            <circle className={styles.label} cx="-8" cy="-1" r="2.6" />
            <circle className={styles.nick} cx="-8" cy="-7" r="1.1" />
          </g>
        </g>
      );

    case 'rewind':
      // A record plus an upright « badge. Added for `rewind`: a disc spinning
      // backwards is only legible if you already know which way "forwards"
      // was, and at 100px nobody does. The badge removes the ambiguity, and
      // sits OUTSIDE the spinning group so it stays readable while the disc
      // counter-rotates underneath it.
      return (
        <g transform="translate(78 74)">
          <g className={styles.rwDisc}>
            <circle className={styles.vinyl} cx="0" cy="0" r="11" />
            <circle className={styles.groove} cx="0" cy="0" r="7.5" />
            <circle className={styles.label} cx="0" cy="0" r="3.2" />
            <circle className={styles.nick} cx="0" cy="-7" r="1.2" />
          </g>
          <g className={styles.rwBadge}>
            <path d="M3 -5 L-3 0 L3 5" />
            <path d="M10 -5 L4 0 L10 5" />
          </g>
        </g>
      );

    case 'glasses':
      // 3D glasses, sat on the visor. NOTE: the prop slot is a sibling of the
      // head, not a child, so it does not inherit head rotation. The watching
      // animations therefore move .prop in lockstep with .head rather than
      // tilting the head freely — see the watching section in the CSS.
      return (
        <g transform="translate(50 28)">
          <rect className={styles.lensA} x="-16" y="-6" width="14" height="12" rx="3" />
          <rect className={styles.lensB} x="2" y="-6" width="14" height="12" rx="3" />
          <line className={styles.bridge} x1="-2" y1="-1" x2="2" y2="-1" />
          <line className={styles.bridge} x1="-16" y1="-3" x2="-22" y2="-5" />
          <line className={styles.bridge} x1="16" y1="-3" x2="22" y2="-5" />
        </g>
      );

    case 'padlock':
      // Held up beside the head: the gesture only reads if the prop is clear of
      // the torso silhouette.
      return (
        <g transform="translate(81 57)">
          <path className={styles.shackle} d="M-5 -4 V-8 a5 5 0 0 1 10 0 V-4" />
          <rect className={styles.lockBody} x="-7.5" y="-4" width="15" height="12" rx="3" />
          <circle className={styles.keyhole} cx="0" cy="1.5" r="1.8" />
        </g>
      );

    case 'question':
      // Stroked path, not a <text> glyph — a text mark would inherit whatever
      // font happened to load and shift size between first paint and webfont.
      return (
        <g transform="translate(76 9)">
          <path className={styles.queryMark} d="M-4.5 -4 a4.5 4.5 0 1 1 4.5 5.4 V3.5" />
          <circle className={styles.queryDot} cx="0" cy="8" r="1.5" />
        </g>
      );

    case 'zzz':
      // Three Zs at rising size. Each is its own group so the CSS can stagger
      // them; the staggering is where the "sleeping" read comes from.
      return (
        <g transform="translate(70 12)">
          <g className={styles.z1}><Z /></g>
          <g className={styles.z2}><Z /></g>
          <g className={styles.z3}><Z /></g>
        </g>
      );

    default:
      return null;
  }
}

/** A single Z, drawn around (0,0) so CSS can place and scale it freely. */
function Z() {
  return <path className={styles.zGlyph} d="M-3.5 -3.5 H3.5 L-3.5 3.5 H3.5" />;
}
