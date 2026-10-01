import styles from './BitScene.module.css';

/**
 * Bit's world: the furniture along the bottom of the player.
 *
 * Drawn in ISOMETRIC projection. Depth runs up-and-right along one fixed
 * vector, and every piece uses the same one — which is the whole reason the
 * room reads as a single space instead of three unrelated props:
 *
 *     depth = (+14, -8)        a shallow ~30 degrees
 *
 * So a box is three faces: a front rect, a top parallelogram offset along
 * that vector, and a right side joining them. Each face takes a different
 * tone — top lightest, front mid, side darkest — because without that
 * separation an isometric box collapses back into a flat hexagon.
 *
 * Circles follow the same rule. A platter lying on a desk top is an ellipse
 * squashed to the projection (ry is about rx/2), while a speaker cone on a
 * vertical front face stays a true circle.
 *
 * Everything here is SET DRESSING — it never moves and reacts only to the
 * beat. Bit travels between these pieces, which is what turns a mascot
 * bobbing on the spot into a character who works a room.
 *
 * Deliberately NOT part of BitRig: the rig is one character driven by a single
 * animation class, and bolting a room onto it would mean all 34 animations had
 * to leave the furniture alone. Separate elements also let the decks keep
 * spinning while Bit is across the room.
 *
 * All of it is floor-anchored. The dock is far taller than the furniture on
 * purpose: the space above is headroom for records thrown in the air.
 */
export default function BitScene({ playing, using }) {
  /*
   * Bit brings his OWN animated crate and deck as props, and performs at the
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

      <Crates style={standBy(using === 'crate')} />
      <Booth playing={playing} style={standBy(using === 'deck')} />

      {/* A stereo pair bookending the room. Both run off --beat, the right one
          half a beat behind, so the pair reads as a groove rather than two
          things twitching in unison. */}
      <Speaker className={styles.speakerL} />
      <Speaker className={styles.speakerR} />
    </div>
  );
}

/* --------------------------------------------------------- record library */

/**
 * An open crate of records, seen from above-front.
 *
 * Paint order is load-bearing: the sleeves are drawn between the top face and
 * the front face, so the front rim overlaps their bottoms and they read as
 * sitting INSIDE the box rather than glued onto it.
 */
function Crates({ style }) {
  const sleeves = [0, 1, 2, 3, 4, 5, 6, 7];

  return (
    <svg className={styles.crates} style={style} viewBox="0 0 104 84" aria-hidden="true">
      {/* Contact shadow, squashed onto the ground plane. */}
      <ellipse className={styles.groundShadow} cx="48" cy="76" rx="40" ry="6" />

      {/* Back shelf: same projection, dimmed and set back for depth. */}
      <g className={styles.far}>
        <path className={styles.faceTop} d="M10 30 L46 30 L58 23 L22 23 Z" />
        <path className={styles.faceFront} d="M10 30 L46 30 L46 48 L10 48 Z" />
        <path className={styles.faceSide} d="M46 30 L58 23 L58 41 L46 48 Z" />
      </g>

      {/* Top face first — the opening the sleeves rise out of. */}
      <path className={styles.faceTop} d="M6 48 L62 48 L76 40 L20 40 Z" />

      {sleeves.map((i) => {
        const x = 14 + i * 6;
        const lift = i === 4 ? 5 : 0; // one pulled proud, so the crate reads as in use
        const top = 24 - lift;
        const bot = 46 - lift;
        return (
          <g key={i}>
            <path
              className={i === 4 ? styles.sleeveProud : styles.sleeve}
              d={`M${x} ${bot} L${x + 4} ${bot} L${x + 4} ${top} L${x} ${top} Z`}
            />
            {/* Lit edge along the depth vector — gives each sleeve thickness. */}
            <path
              className={styles.sleeveEdge}
              d={`M${x + 4} ${bot} L${x + 7} ${bot - 2} L${x + 7} ${top - 2} L${x + 4} ${top} Z`}
            />
          </g>
        );
      })}

      {/* Front and side last, so they clip the sleeves at the rim. */}
      <path className={styles.faceFront} d="M6 48 L62 48 L62 72 L6 72 Z" />
      <path className={styles.faceSide} d="M62 48 L76 40 L76 64 L62 72 Z" />

      {/* A record leaning against the crate. */}
      <g className={styles.leaner}>
        <ellipse className={styles.leanerDisc} cx="88" cy="58" rx="11" ry="13" />
        <ellipse className={styles.leanerLabel} cx="88" cy="58" rx="3.4" ry="4" />
      </g>
    </svg>
  );
}

/* ---------------------------------------------------------------- decks */

function Booth({ playing, style }) {
  return (
    <svg className={styles.booth} style={style} viewBox="0 0 190 96" aria-hidden="true">
      <ellipse className={styles.groundShadow} cx="92" cy="86" rx="80" ry="7" />

      {/* Desk: top surface, then the panels beneath it. */}
      <path className={styles.faceTop} d="M8 50 L164 50 L182 40 L26 40 Z" />
      <path className={styles.faceFront} d="M8 50 L164 50 L164 80 L8 80 Z" />
      <path className={styles.faceSide} d="M164 50 L182 40 L182 70 L164 80 Z" />
      {/* Lip along the leading edge — stops the desk reading as a flat slab. */}
      <path className={styles.lip} d="M8 50 L164 50 L164 54 L8 54 Z" />

      {[46, 130].map((cx) => (
        <Deck key={cx} cx={cx} playing={playing} />
      ))}

      {/* Mixer: a small box standing on the desk, same projection. */}
      <g>
        <path className={styles.faceTop} d="M80 46 L108 46 L118 40 L90 40 Z" />
        <path className={styles.mixerFront} d="M80 46 L108 46 L108 64 L80 64 Z" />
        <path className={styles.faceSide} d="M108 46 L118 40 L118 58 L108 64 Z" />
        <rect className={styles.faderSlot} x="85" y="50" width="2.6" height="11" rx="1.3" />
        <rect className={styles.faderSlot} x="93" y="50" width="2.6" height="11" rx="1.3" />
        <rect className={styles.faderSlot} x="101" y="50" width="2.6" height="11" rx="1.3" />
        <circle className={styles.led} cx="86.3" cy="54" r="1.9" />
        <circle className={styles.led} cx="94.3" cy="57" r="1.9" />
        <circle className={styles.led} cx="102.3" cy="52" r="1.9" />
      </g>
    </svg>
  );
}

/**
 * One turntable lying on the desk top.
 *
 * The platter is a STATIC ellipse; what spins is a nick orbiting inside it.
 * Rotating the ellipse itself would wobble, because a rotated ellipse is a
 * different shape — but a point orbiting on a squashed circle is exactly what
 * a spinning disc looks like in projection.
 *
 * The squash lives in a transform ATTRIBUTE on an outer group that no selector
 * touches. A CSS transform would override the attribute outright and flatten
 * the orbit back to a circle.
 */
function Deck({ cx, playing }) {
  return (
    <g>
      <ellipse className={styles.platter} cx={cx} cy="45" rx="19" ry="10" />
      <ellipse className={styles.disc} cx={cx} cy="45" rx="16" ry="8.4" />
      <ellipse className={styles.groove} cx={cx} cy="45" rx="12" ry="6.3" />
      <ellipse className={styles.groove} cx={cx} cy="45" rx="8" ry="4.2" />
      <ellipse className={styles.discLabel} cx={cx} cy="45" rx="4.6" ry="2.4" />

      <g transform={`translate(${cx} 45) scale(1 0.525)`}>
        <g className={playing ? styles.orbit : undefined}>
          <circle className={styles.nick} cx="13" cy="0" r="1.7" />
        </g>
      </g>

      {/* Tonearm, angled along the projection so it sits ON the deck. */}
      <path className={styles.tonearm} d={`M${cx + 20} 36 L${cx + 8} 41 L${cx + 2} 44`} />
      <circle className={styles.pivot} cx={cx + 20} cy="36" r="2.6" />
    </g>
  );
}

/* ------------------------------------------------------------- speakers */

/** One cabinet. Cones push on the beat; see BitScene.module.css. */
function Speaker({ className }) {
  return (
    <svg className={`${styles.speaker} ${className}`} viewBox="0 0 70 118" aria-hidden="true">
      <ellipse className={styles.groundShadow} cx="30" cy="108" rx="26" ry="5" />

      <path className={styles.faceTop} d="M6 18 L48 18 L62 10 L20 10 Z" />
      <path className={styles.faceFront} d="M6 18 L48 18 L48 104 L6 104 Z" />
      <path className={styles.faceSide} d="M48 18 L62 10 L62 96 L48 104 Z" />

      {/* True circles: these sit on a vertical face, so the projection does
          not foreshorten them. */}
      <circle className={styles.cone} cx="27" cy="46" r="15" />
      <circle className={styles.coneInner} cx="27" cy="46" r="5.5" />
      <circle className={styles.tweeter} cx="27" cy="84" r="8" />
      <circle className={styles.coneInner} cx="27" cy="84" r="3" />
    </svg>
  );
}
