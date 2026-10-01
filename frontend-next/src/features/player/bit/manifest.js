import styles from './animations.module.css';

/**
 * The index of everything Bit can do.
 *
 * This file is the contract between the rig (which only knows geometry) and the
 * dock's state machine (which only knows what just happened). The picker chooses
 * a state, reads the variants here, and hands the resolved class to <BitRig />.
 *
 * Two invariants that nothing enforces at runtime, so they are checked by hand:
 *
 *   1. Every key listed in ANIMATIONS must have a matching `.key` rule in
 *      animations.module.css. A typo does NOT throw — classFor() returns '' and
 *      Bit simply stands still, which is indistinguishable from "this state has
 *      no animation yet". That silence is why the keys are spelled out as
 *      strings rather than derived from Object.keys(styles): the authored list
 *      is the thing worth diffing.
 *   2. Every key must also appear in META, because a missing entry means the
 *      dock cannot tell a one-shot from a loop and will either leave a finished
 *      celebration frozen on screen or restart it forever.
 *
 * ms values are not estimates. For one-shots, the CSS declares a single `--t`
 * on the state class and every limb in that state inherits it, so the number
 * below is the same number as the `--t` in the stylesheet — one place to look
 * when a duration changes.
 */

export const ANIMATIONS = {
  /* Nothing loaded / player parked. Eyes shut in all three. */
  asleep: ['a_asleep_slump', 'a_asleep_breathe', 'a_asleep_twitch'],

  /* A track is loaded but paused. The only states allowed to look aimless. */
  idle: ['a_idle_foottap', 'a_idle_lookaround', 'a_idle_stretch', 'a_idle_scratch'],

  /* Playing. ORDER IS MEANINGFUL: low energy -> high. The picker maps the
     track's mood band onto this index, so inserting a variant in the middle
     re-bands every song. Append, or re-tune the picker deliberately. */
  groove: [
    'a_groove_sway',
    'a_groove_nod',
    'a_groove_bob',
    'a_groove_bounce',
    'a_groove_headbang',
  ],

  /* Choosing the next track. All loops — the wait has no known length. */
  digging: ['a_digging_flip', 'a_digging_dive', 'a_digging_pull'],

  /* Track played to the end. The record does the work in all three — a body
     move with a disc parked in the hand did not read as celebration. */
  celebrate: ['a_celebrate_spin', 'a_celebrate_toss', 'a_celebrate_jump'],

  /* Skipped inside the first 15%. A rejection, so all three destroy something:
     the record is thrown out of frame, the deck is slammed, or the whole set
     gets flipped. (The earlier shrug/toss/turn set was replaced outright — a
     shrug is not legible at a glance and read as idle.) */
  sulk: ['a_sulk_hurl', 'a_sulk_slam', 'a_sulk_flip'],

  /* A track was queued. */
  catch: ['a_catch_grab', 'a_catch_juggle'],

  /* Video mode. */
  watching: ['a_watching_shades', 'a_watching_lean'],

  /* Verify clip playing. */
  listening: ['a_listening_press', 'a_listening_nod'],

  /* Error / track unavailable. */
  confused: ['a_confused_tilt', 'a_confused_static', 'a_confused_scratch'],

  /* Spotify-only: metadata but no streamable audio. */
  locked: ['a_locked_hold', 'a_locked_shake'],

  /* Previous pressed. */
  rewind: ['a_rewind_moonwalk', 'a_rewind_spinback'],
};

/**
 * `loop: true`  -> runs until the dock changes state; `ms` is null because the
 *                  duration is either a fixed loop or driven by --beat.
 * `loop: false` -> a one-shot; `ms` is exactly how long before it is done, and
 *                  the dock uses it to fall back to the base state.
 *
 * Groove entries are all `ms: null` for a second reason beyond looping: their
 * duration is var(--beat), which is rewritten per track. There is no fixed
 * number to record.
 */
export const META = {
  a_asleep_slump:     { loop: true,  ms: null },
  a_asleep_breathe:   { loop: true,  ms: null },
  a_asleep_twitch:    { loop: true,  ms: null },

  a_idle_foottap:     { loop: true,  ms: null },
  a_idle_lookaround:  { loop: true,  ms: null },
  a_idle_stretch:     { loop: false, ms: 1400 },
  a_idle_scratch:     { loop: false, ms: 1600 },

  a_groove_sway:      { loop: true,  ms: null },
  a_groove_nod:       { loop: true,  ms: null },
  a_groove_bob:       { loop: true,  ms: null },
  a_groove_bounce:    { loop: true,  ms: null },
  a_groove_headbang:  { loop: true,  ms: null },

  a_digging_flip:     { loop: true,  ms: null },
  a_digging_dive:     { loop: true,  ms: null },
  a_digging_pull:     { loop: true,  ms: null },

  a_celebrate_spin:   { loop: false, ms: 1200 },
  a_celebrate_toss:   { loop: false, ms: 1300 },
  a_celebrate_jump:   { loop: false, ms: 1100 },

  a_sulk_hurl:        { loop: false, ms: 1100 },
  a_sulk_slam:        { loop: false, ms: 1200 },
  a_sulk_flip:        { loop: false, ms: 1300 },

  a_catch_grab:       { loop: false, ms: 850 },
  a_catch_juggle:     { loop: false, ms: 1300 },

  a_watching_shades:  { loop: true,  ms: null },
  a_watching_lean:    { loop: true,  ms: null },

  a_listening_press:  { loop: true,  ms: null },
  a_listening_nod:    { loop: true,  ms: null },

  a_confused_tilt:    { loop: false, ms: 1300 },
  a_confused_static:  { loop: true,  ms: null },
  a_confused_scratch: { loop: false, ms: 1600 },

  a_locked_hold:      { loop: false, ms: 1500 },
  a_locked_shake:     { loop: false, ms: 1300 },

  a_rewind_moonwalk:  { loop: false, ms: 1300 },
  a_rewind_spinback:  { loop: false, ms: 900 },
};

/**
 * Props that pair naturally with a state, as a convenience for the picker.
 * Advisory only — the dock owns the final call, since some of these depend on
 * context the rig has no idea about (a sulk only throws a record if one was
 * actually on screen). States absent from this map want no prop.
 */
export const SUGGESTED_PROP = {
  asleep: 'zzz',
  digging: 'crate',
  celebrate: 'record',
  // `deck`, not `record`: a rejection needs something with enough mass to be
  // worth destroying, and one variant flips the entire set over.
  sulk: 'deck',
  catch: 'record',
  watching: 'glasses',
  confused: 'question',
  locked: 'padlock',
  // `rewind`, not `record`: a disc spinning backwards is ambiguous at this
  // size, so the prop carries an upright « badge that states the direction.
  rewind: 'rewind',
};

/** Resolve a manifest key to its hashed CSS-module class. */
export const classFor = (key) => styles[key] || '';
