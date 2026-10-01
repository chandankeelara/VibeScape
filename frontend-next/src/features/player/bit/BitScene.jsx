import styles from './BitScene.module.css';

/**
 * Bit's world: the furniture along the bottom of the player.
 *
 * Everything here is SET DRESSING — it never moves location and never reacts
 * to state, only to the beat. Bit himself travels between these pieces, which
 * is what turns a mascot bobbing on the spot into a character who works in a
 * room: he digs AT the crates, plays AT the decks.
 *
 * Deliberately NOT in BitRig: the rig is one character whose every part is
 * driven by a single animation class, and bolting a room onto it would mean
 * every one of the 34 animations had to leave the furniture alone. Separate
 * elements also let the decks keep spinning while Bit is across the room.
 *
 * All of it is anchored to the floor. The dock is far taller than the
 * furniture on purpose — the space above is headroom for records thrown in
 * the air, and must stay empty.
 */
export default function BitScene({ playing, using }) {
  /*
   * Bit brings his OWN animated crate and deck as props, and he performs at
   * the exact spot the static one stands. Drawing both gives two crates a
   * few pixels apart, or — worse — lets him flip a phantom deck while the
   * real booth sits untouched beside it.
   *
   * So the static piece stands down while he is handling his copy. Read as a
   * cross-fade it looks like the furniture coming alive, which is the effect
   * we wanted anyway.
   */
  const hideCrates = using === 'crate';
  const hideBooth = using === 'deck';

  /*
   * Inline rather than a class. A .standBy class tied with the .booth rule on
   * specificity and lost in the cascade — the class landed on the element and
   * matched it, but opacity and transform both stayed at their defaults. An
   * inline style cannot lose that argument, and this is one declaration on one
   * element, not a pattern worth generalising.
   */
  const standBy = (hide) => (hide ? { opacity: 0, transform: 'translateY(6px)' } : undefined);
  return (
    <div className={styles.scene} aria-hidden="true">
      {/* A horizon rather than a hard line: the stage above has no border, so
          a drawn floor would read as a separate panel bolted to the page. */}
      <div className={styles.floor} />

      {/* ---------------------------------------------------- record library */}
      <svg className={styles.crates} style={standBy(hideCrates)} viewBox="0 0 90 70">
        {/* Back shelf, slightly smaller and dimmer — cheap depth. */}
        <g className={styles.shelfBack}>
          <rect x="8" y="22" width="34" height="26" rx="2" />
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <rect key={i} className={styles.sleeveBack} x={11 + i * 5} y="25" width="3.4" height="20" rx="1" />
          ))}
        </g>

        <g className={styles.crate}>
          <rect x="4" y="40" width="52" height="28" rx="3" />
          {[0, 1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
            <rect key={i} className={styles.sleeve} x={8 + i * 5.2} y="43" width="3.6" height="22" rx="1" />
          ))}
          {/* One pulled proud of the row, so the crate reads as "being used"
              rather than as a solid block. */}
          <rect className={styles.sleeveOut} x="34" y="39" width="3.6" height="23" rx="1" />
        </g>

        {/* A record leaning against the crate. */}
        <g className={styles.leaner}>
          <circle cx="68" cy="56" r="11" />
          <circle className={styles.leanerLabel} cx="68" cy="56" r="3.4" />
        </g>
      </svg>

      {/* -------------------------------------------------------- the decks */}
      <svg className={styles.booth} style={standBy(hideBooth)} viewBox="0 0 150 70">
        <rect className={styles.boothBody} x="2" y="30" width="146" height="38" rx="4" />
        {/* Front panel highlight — stops the booth reading as a flat slab. */}
        <rect className={styles.boothLip} x="2" y="30" width="146" height="4" rx="2" />

        {[26, 112].map((cx) => (
          <g key={cx}>
            <circle className={styles.platter} cx={cx} cy="46" r="15" />
            <circle className={`${styles.disc} ${playing ? styles.discSpin : ''}`} cx={cx} cy="46" r="12.5" />
            <circle className={styles.discLabel} cx={cx} cy="46" r="4" />
            {/* Off-centre nick: a perfect circle gives no sense of rotation,
                so without it the spin is invisible. */}
            <circle className={styles.discNick} cx={cx} cy="37" r="1.1" />
            <line className={styles.tonearm} x1={cx + 15} y1="34" x2={cx + 4} y2="42" />
          </g>
        ))}

        {/* Mixer: faders and a bank of level LEDs that run off the beat. */}
        <g className={styles.mixer}>
          <rect x="60" y="36" width="30" height="26" rx="2" />
          <rect className={styles.fader} x="65" y="40" width="3" height="18" rx="1.5" />
          <rect className={styles.fader} x="73" y="40" width="3" height="18" rx="1.5" />
          <rect className={styles.fader} x="81" y="40" width="3" height="18" rx="1.5" />
          <circle className={styles.knob} cx="66.5" cy="47" r="2.2" />
          <circle className={styles.knob} cx="74.5" cy="51" r="2.2" />
          <circle className={styles.knob} cx="82.5" cy="45" r="2.2" />
        </g>
      </svg>

      {/* ------------------------------------------------------ speaker stacks */}
      {/* A stereo pair bookending the room. Both run off --beat, but the right
          one is offset half a beat so the pair reads as a groove rather than
          two things twitching in unison. */}
      <Speaker className={styles.speakerL} />
      <Speaker className={styles.speakerR} />
    </div>
  );
}

/** One cabinet. Cones push on the beat; see BitScene.module.css. */
function Speaker({ className }) {
  return (
    <svg className={`${styles.speaker} ${className}`} viewBox="0 0 48 92" aria-hidden="true">
      <rect className={styles.cab} x="4" y="8" width="40" height="80" rx="4" />
      <circle className={styles.cone} cx="24" cy="32" r="13" />
      <circle className={styles.coneInner} cx="24" cy="32" r="5" />
      <circle className={styles.tweeter} cx="24" cy="66" r="7" />
      <circle className={styles.coneInner} cx="24" cy="66" r="2.6" />
    </svg>
  );
}
