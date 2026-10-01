import { useId } from 'react';
import styles from './BitScene.module.css';

/**
 * Bit's world: the DJ booth, record library and speakers along the bottom of
 * the player.
 *
 * ISOMETRIC. Depth runs up-and-right on one fixed vector — every piece uses
 * the same one, which is the whole reason the room reads as a single space
 * rather than three unrelated props:
 *
 *     depth = (+14, -8)        a shallow ~30 degrees
 *
 * Three things do the heavy lifting, in order of how much they matter:
 *
 *   1. GRADIENTS, not flat fills. A flat-shaded parallelogram reads as a
 *      sticker however correct its geometry; a face that darkens away from
 *      the light reads as a surface. Every box face is a gradient.
 *   2. SPECIFIC GEAR, not generic boxes. A turntable needs a counterweight,
 *      a headshell, a pitch fader and strobe dots or it is just a circle on
 *      a slab. The detail is what says "someone who knows this made it".
 *   3. EDGE LIGHT. A one-pixel lit line along every top-front edge, which is
 *      what separates two adjoining faces without an outline doing it.
 *
 * Detail is chosen for ~110-140px tall, where this actually renders. Anything
 * finer than about a unit and a half turns to mush, so the budget goes on
 * silhouette and two or three focal points per piece rather than on realism.
 *
 * Everything is SET DRESSING: it never moves and reacts only to the beat.
 * Bit travels between these pieces, which is what turns a mascot bobbing on
 * the spot into a character who works a room.
 *
 * All floor-anchored. The dock is far taller than the furniture on purpose —
 * the space above is headroom for records thrown in the air.
 */
export default function BitScene({ playing, using }) {
  /*
   * Gradient ids are document-global. Each piece is its own <svg> and needs
   * its own <defs>, so everything is namespaced per instance — the lab
   * renders four rooms at once and they would otherwise share one set.
   */
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '');

  /*
   * Bit brings his OWN animated crate and deck as props and performs at the
   * exact spot the static one stands. Drawing both gives two crates a few
   * pixels apart, or lets him flip a phantom deck while the real booth sits
   * untouched beside it. So the static piece stands down while he handles his
   * copy; read as a cross-fade it looks like the furniture coming alive.
   *
   * Inline rather than a class: as a class this tied with .booth on
   * specificity and lost.
   */
  const standBy = (hide) => (hide ? { opacity: 0, transform: 'translateY(6px)' } : undefined);

  return (
    <div className={styles.scene} aria-hidden="true">
      {/* A horizon rather than a hard line — the stage above has no border, so
          a drawn floor would read as a panel bolted onto the page. */}
      <div className={styles.floor} />

      <Library uid={uid} style={standBy(using === 'crate')} />
      <Booth uid={uid} playing={playing} style={standBy(using === 'deck')} />
      <Speaker uid={uid} side="L" className={styles.speakerL} />
      <Speaker uid={uid} side="R" className={styles.speakerR} />
    </div>
  );
}

/* ---------------------------------------------------------------- shading */

/**
 * The shared material library.
 *
 * Rendered once inside every piece's <svg>. The light is up and slightly
 * left, so tops are brightest, fronts mid, right sides darkest — consistency
 * here is what stops the three pieces looking like they were lit separately.
 */
function Shading({ uid }) {
  return (
    <defs>
      {/* x1/y1 -> x2/y2 follow the depth vector, so the falloff runs with the
          form instead of straight down. */}
      <linearGradient id={`${uid}-top`} x1="0" y1="1" x2="0.6" y2="0">
        <stop offset="0" className={styles.topA} />
        <stop offset="1" className={styles.topB} />
      </linearGradient>

      <linearGradient id={`${uid}-front`} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" className={styles.frontA} />
        <stop offset="1" className={styles.frontB} />
      </linearGradient>

      <linearGradient id={`${uid}-side`} x1="0" y1="0" x2="1" y2="0.4">
        <stop offset="0" className={styles.sideA} />
        <stop offset="1" className={styles.sideB} />
      </linearGradient>

      {/* Platter sheen: a soft bright patch up-left, falling off fast. */}
      <radialGradient id={`${uid}-sheen`} cx="0.34" cy="0.3" r="0.72">
        <stop offset="0" className={styles.sheenA} />
        <stop offset="1" className={styles.sheenB} />
      </radialGradient>

      {/* Speaker cone: dark at the rim, lifting toward the dust cap. */}
      <radialGradient id={`${uid}-cone`} cx="0.4" cy="0.34" r="0.7">
        <stop offset="0" className={styles.coneA} />
        <stop offset="1" className={styles.coneB} />
      </radialGradient>

      {/* Grille mesh, as a tile rather than hundreds of circles. */}
      <pattern id={`${uid}-mesh`} width="3" height="3" patternUnits="userSpaceOnUse">
        <circle cx="1" cy="1" r="0.62" className={styles.meshDot} />
      </pattern>
    </defs>
  );
}

/* --------------------------------------------------------- record library */

/**
 * A two-tier rack, not a box with lines on it.
 *
 * Sleeves vary in tint and height because a rack of identical spines reads as
 * a barcode. One is pulled proud and one leans out of the top tier, which is
 * what says the rack is used rather than stocked.
 */
function Library({ uid, style }) {
  const lower = [0, 1, 2, 3, 4, 5, 6, 7, 8];
  const upper = [0, 1, 2, 3, 4, 5, 6];

  return (
    <svg className={styles.crates} style={style} viewBox="0 0 112 96" aria-hidden="true">
      <Shading uid={uid} />
      <ellipse className={styles.groundPool} cx="52" cy="88" rx="46" ry="7" />

      {/* ---- upper tier ---- */}
      <path className={styles.side} fill={`url(#${uid}-side)`} d="M62 22 L76 14 L76 44 L62 52 Z" />
      <path className={styles.top} fill={`url(#${uid}-top)`} d="M8 22 L62 22 L76 14 L22 14 Z" />
      {upper.map((i) => (
        <g key={i}>
          <rect className={styles[`spine${i % 4}`]} x={12 + i * 6.6} y={-2 + 24} width="4.4" height="24" rx="0.6" />
          <path className={styles.spineEdge}
            d={`M${16.4 + i * 6.6} 46 L${19 + i * 6.6} 44.5 L${19 + i * 6.6} 20.5 L${16.4 + i * 6.6} 22 Z`} />
        </g>
      ))}
      <path className={styles.front} fill={`url(#${uid}-front)`} d="M8 22 L62 22 L62 52 L8 52 Z" />
      <path className={styles.lip} d="M8 22 L62 22 L62 25 L8 25 Z" />

      {/* ---- lower tier ---- */}
      <path className={styles.side} fill={`url(#${uid}-side)`} d="M62 52 L76 44 L76 78 L62 86 Z" />
      <path className={styles.top} fill={`url(#${uid}-top)`} d="M8 52 L62 52 L76 44 L22 44 Z" />
      {lower.map((i) => (
        <g key={i}>
          <rect
            className={i === 5 ? styles.spineProud : styles[`spine${(i + 2) % 4}`]}
            x={11 + i * 5.9}
            y={i === 5 ? 48 : 53}
            width="4.2"
            height={i === 5 ? 30 : 27}
            rx="0.6"
          />
          <path className={styles.spineEdge}
            d={`M${15.2 + i * 5.9} ${i === 5 ? 78 : 80} L${17.8 + i * 5.9} ${i === 5 ? 76.5 : 78.5}
                L${17.8 + i * 5.9} ${i === 5 ? 46.5 : 51.5} L${15.2 + i * 5.9} ${i === 5 ? 48 : 53} Z`} />
        </g>
      ))}
      <path className={styles.front} fill={`url(#${uid}-front)`} d="M8 52 L62 52 L62 82 L8 82 Z" />
      <path className={styles.lip} d="M8 52 L62 52 L62 55 L8 55 Z" />

      {/* Feet, so the rack stands on the floor instead of melting into it. */}
      <path className={styles.foot} d="M10 82 L16 82 L16 86 L10 86 Z" />
      <path className={styles.foot} d="M54 82 L60 82 L60 86 L54 86 Z" />

      {/* A record leaning against the rack, and a flat stack beside it. */}
      <g className={styles.leaner}>
        <ellipse className={styles.vinylEdge} cx="92" cy="66" rx="13" ry="15" />
        <ellipse className={styles.vinyl} cx="91" cy="66" rx="13" ry="15" />
        <ellipse className={styles.vinylSheen} cx="91" cy="66" rx="13" ry="15" fill={`url(#${uid}-sheen)`} />
        <ellipse className={styles.vinylLabel} cx="91" cy="66" rx="4" ry="4.6" />
      </g>
      <g>
        <ellipse className={styles.stackDisc} cx="92" cy="84" rx="14" ry="5" />
        <ellipse className={styles.stackDisc} cx="92" cy="81" rx="14" ry="5" />
        <ellipse className={styles.stackTop} cx="92" cy="78" rx="14" ry="5" />
        <ellipse className={styles.vinylLabel} cx="92" cy="78" rx="4" ry="1.5" />
      </g>
    </svg>
  );
}

/* ------------------------------------------------------------- the booth */

function Booth({ uid, playing, style }) {
  return (
    <svg className={styles.booth} style={style} viewBox="0 0 210 104" aria-hidden="true">
      <Shading uid={uid} />
      <ellipse className={styles.groundPool} cx="102" cy="96" rx="92" ry="8" />

      {/* Plinth. The lip along the leading edge is the single mark that stops
          a desk reading as a flat slab. */}
      <path className={styles.side} fill={`url(#${uid}-side)`} d="M180 54 L198 44 L198 86 L180 96 Z" />
      <path className={styles.top} fill={`url(#${uid}-top)`} d="M12 54 L180 54 L198 44 L30 44 Z" />
      <path className={styles.front} fill={`url(#${uid}-front)`} d="M12 54 L180 54 L180 96 L12 96 Z" />
      <path className={styles.lip} d="M12 54 L180 54 L180 58 L12 58 Z" />
      <path className={styles.foot} d="M16 96 L26 96 L26 100 L16 100 Z" />
      <path className={styles.foot} d="M166 96 L176 96 L176 100 L166 100 Z" />

      <Deck uid={uid} cx={48} playing={playing} />
      <Deck uid={uid} cx={146} playing={playing} mirrored />
      <Mixer uid={uid} />
    </svg>
  );
}

/**
 * One turntable.
 *
 * The platter is a STATIC ellipse and what spins is a nick orbiting inside
 * it. Rotating the ellipse would wobble, because a rotated ellipse is a
 * different shape — but a point orbiting a squashed circle is exactly what a
 * spinning disc looks like in projection.
 *
 * The squash lives in a transform ATTRIBUTE on an outer group no selector
 * touches. A CSS transform would override the attribute outright and flatten
 * the orbit back to a circle.
 */
function Deck({ uid, cx, playing, mirrored }) {
  const dir = mirrored ? -1 : 1;

  return (
    <g>
      {/* Deck body sunk into the plinth. */}
      <ellipse className={styles.deckWell} cx={cx} cy="49" rx="27" ry="14" />

      {/* Strobe dots around the rim — the detail that says "turntable"
          rather than "circle", and it costs eight marks. */}
      {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => {
        const a = (i / 8) * Math.PI * 2;
        return (
          <circle
            key={i}
            className={playing ? styles.strobeOn : styles.strobe}
            cx={cx + Math.cos(a) * 24}
            cy={49 + Math.sin(a) * 12.4}
            r="0.9"
            style={{ animationDelay: `calc(var(--beat, 0.5s) * ${(i * 0.12).toFixed(2)})` }}
          />
        );
      })}

      <ellipse className={styles.platterRim} cx={cx} cy="49" rx="21" ry="11" />
      <ellipse className={styles.slipmat} cx={cx} cy="49" rx="18.5" ry="9.7" />
      <ellipse className={styles.vinyl} cx={cx} cy="49" rx="16.5" ry="8.6" />
      <ellipse className={styles.vinylSheen} cx={cx} cy="49" rx="16.5" ry="8.6" fill={`url(#${uid}-sheen)`} />
      <ellipse className={styles.groove} cx={cx} cy="49" rx="12.5" ry="6.5" />
      <ellipse className={styles.groove} cx={cx} cy="49" rx="9" ry="4.7" />
      <ellipse className={styles.vinylLabel} cx={cx} cy="49" rx="4.8" ry="2.5" />
      <circle className={styles.spindle} cx={cx} cy="49" r="0.9" />

      <g transform={`translate(${cx} 49) scale(1 0.52)`}>
        <g className={playing ? styles.orbit : undefined}>
          <circle className={styles.nick} cx="13.5" cy="0" r="1.6" />
          {/* Invisible counterweight: keeps the group's bbox symmetric so
              transform-origin:center lands on the spindle, not on the nick. */}
          <circle cx="-13.5" cy="0" r="1.6" fill="none" stroke="none" />
        </g>
      </g>

      {/* Tonearm: pivot, counterweight, S-arm, headshell. Four marks, and
          without them a turntable is a record player. */}
      <g>
        <ellipse className={styles.armBase} cx={cx + 24 * dir} cy="40" rx="4.4" ry="3" />
        <path className={styles.armWeight} d={`M${cx + 27 * dir} 38 h${3.4 * dir} v3.4 h${-3.4 * dir} Z`} />
        <path
          className={styles.armTube}
          d={`M${cx + 23 * dir} 40 Q${cx + 14 * dir} 42 ${cx + 8 * dir} 46`}
        />
        <path className={styles.headshell} d={`M${cx + 9 * dir} 44.6 l${4 * dir} 1.2 l${-1.6 * dir} 2.6 l${-4 * dir} -1.2 Z`} />
      </g>

      {/* Pitch fader and start button — the two controls a DJ touches most. */}
      <rect className={styles.pitchSlot} x={cx + (mirrored ? 14 : -20)} y="62" width="6" height="15" rx="3" />
      <rect className={styles.pitchKnob} x={cx + (mirrored ? 13.2 : -20.8)} y="68" width="7.6" height="3" rx="1.5" />
      <circle className={playing ? styles.startOn : styles.start} cx={cx + (mirrored ? -17 : 17)} cy="69" r="3.4" />
    </g>
  );
}

/** Two-channel mixer: EQ knobs, channel faders, crossfader, VU ladder. */
function Mixer({ uid }) {
  return (
    <g>
      <path className={styles.side} fill={`url(#${uid}-side)`} d="M118 50 L128 44 L128 76 L118 82 Z" />
      <path className={styles.top} fill={`url(#${uid}-top)`} d="M84 50 L118 50 L128 44 L94 44 Z" />
      <path className={styles.mixerFace} d="M84 50 L118 50 L118 82 L84 82 Z" />
      <path className={styles.lip} d="M84 50 L118 50 L118 52.6 L84 52.6 Z" />

      {/* EQ: two channels of three. */}
      {[0, 1].map((ch) =>
        [0, 1, 2].map((row) => (
          <circle
            key={`${ch}-${row}`}
            className={styles.knob}
            cx={91 + ch * 20}
            cy={56 + row * 5.6}
            r="2.1"
          />
        ))
      )}

      {/* Channel faders, then the crossfader across the bottom. */}
      <rect className={styles.faderSlot} x="89.4" y="63" width="3.2" height="12" rx="1.6" />
      <rect className={styles.faderSlot} x="109.4" y="63" width="3.2" height="12" rx="1.6" />
      <rect className={styles.faderCap} x="88" y="66" width="6" height="2.6" rx="1.3" />
      <rect className={styles.faderCap} x="108" y="69" width="6" height="2.6" rx="1.3" />
      <rect className={styles.faderSlot} x="94" y="77" width="14" height="3" rx="1.5" />
      <rect className={styles.faderCap} x="99" y="76.2" width="4.4" height="4.6" rx="1.4" />

      {/* VU ladder. Reads as level because the top segments are tinted hot and
          lag behind the rest — a uniform column just blinks. */}
      {[0, 1, 2, 3, 4].map((i) => (
        <rect
          key={i}
          className={i > 3 ? styles.vuHot : styles.vu}
          x="101"
          y={72 - i * 3.4}
          width="2.4"
          height="2.4"
          rx="0.5"
          style={{ animationDelay: `calc(var(--beat, 0.5s) * ${(i * 0.16).toFixed(2)})` }}
        />
      ))}
    </g>
  );
}

/* ----------------------------------------------------------- the speakers */

/**
 * A floor monitor on a short stand.
 *
 * The cabinet is tilted back by shearing the front face rather than rotating
 * the whole group — rotation would break the shared depth vector and the
 * piece would stop belonging to the room.
 */
function Speaker({ uid, side, className }) {
  return (
    <svg className={`${styles.speaker} ${className}`} viewBox="0 0 76 126" aria-hidden="true">
      <Shading uid={uid} />
      <ellipse className={styles.groundPool} cx="34" cy="120" rx="30" ry="6" />

      {/* Stand */}
      <path className={styles.standCol} d="M30 96 L42 96 L42 116 L30 116 Z" />
      <path className={styles.standFoot} d="M20 116 L52 116 L56 120 L16 120 Z" />

      <path className={styles.side} fill={`url(#${uid}-side)`} d="M52 20 L66 12 L66 92 L52 100 Z" />
      <path className={styles.top} fill={`url(#${uid}-top)`} d="M8 20 L52 20 L66 12 L22 12 Z" />
      <path className={styles.front} fill={`url(#${uid}-front)`} d="M8 20 L52 20 L52 100 L8 100 Z" />
      <path className={styles.lip} d="M8 20 L52 20 L52 23 L8 23 Z" />

      {/* Grille mesh over the baffle, masked to the cabinet front. */}
      <path className={styles.mesh} fill={`url(#${uid}-mesh)`} d="M8 20 L52 20 L52 100 L8 100 Z" />

      {/* Woofer: surround, cone, dust cap. True circles — these sit on a
          vertical face, so the projection does not foreshorten them. */}
      <circle className={styles.driverRing} cx="30" cy="50" r="17" />
      <circle className={styles.cone} cx="30" cy="50" r="14" fill={`url(#${uid}-cone)`} />
      <circle className={styles.dustCap} cx="30" cy="50" r="5" />
      {[0, 1, 2, 3].map((i) => {
        const a = (i / 4) * Math.PI * 2 + 0.4;
        return <circle key={i} className={styles.screw} cx={30 + Math.cos(a) * 15.5} cy={50 + Math.sin(a) * 15.5} r="0.8" />;
      })}

      {/* Tweeter in a shallow horn. */}
      <circle className={styles.driverRing} cx="30" cy="80" r="8.5" />
      <circle className={styles.cone} cx="30" cy="80" r="6.5" fill={`url(#${uid}-cone)`} />
      <circle className={styles.dustCap} cx="30" cy="80" r="2.4" />

      {/* Bass port and power LED. */}
      <ellipse className={styles.port} cx="44" cy="91" rx="4.6" ry="2.4" />
      <circle className={styles.powerLed} cx="14" cy="93" r="1.5" />

      {/* A badge, because real cabinets have one and the eye expects it. */}
      <rect className={styles.badge} x={side === 'L' ? 11 : 11} y="25" width="11" height="3.4" rx="1.7" />
    </svg>
  );
}
