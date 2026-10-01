import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { isMetadataOnly, moodFor } from '../../../lib/vibe';
import { ANIMATIONS, META, SUGGESTED_PROP } from './manifest';
/*
 * Namespace import, deliberately, for one optional export.
 *
 * GROOVE_BY_BAND groups the groove moves by mood band. A NAMED import of an
 * export that does not exist is a hard Rollup build error, which defeats the
 * runtime fallback below entirely — the app would not build at all while the
 * animation library was being extended. Reaching through the namespace makes
 * a missing export a plain `undefined`, which the picker already handles by
 * falling back to the flat list.
 */
import * as manifest from './manifest';

/**
 * Bit's brain.
 *
 * Two layers, deliberately separate:
 *
 *   BASE state   derived from what the player IS doing right now (playing,
 *                loading, video mode...). Continuous — Bit sits in it until
 *                the player changes.
 *   EVENT state  a one-shot reaction to something that just HAPPENED (you
 *                finished a track, skipped one, queued one). Overrides the
 *                base for the length of its animation, then falls back.
 *
 * Keeping them apart is what stops a reaction from stranding Bit: the base is
 * always recomputed from live context, so however an event ends, there is
 * already a correct state underneath it to return to. A single flat state
 * machine would need an explicit transition for every pair.
 *
 * Each state owns a POOL of animations and one is picked per entry, so the
 * same event does not play the same clip twice in a row.
 */

/*
 * Props come from the manifest, not from a list here.
 *
 * Several animations ASSUME the prop is present — a_sulk_hurl throws the
 * record off-screen, a_sulk_flip tips the whole deck over, a_catch_* fly one
 * in, a_confused_tilt pops a question mark. The animation author is therefore the right owner of that mapping,
 * and a second copy in this file would silently drift out of sync with the
 * keyframes it is supposed to feed.
 */

/**
 * Where Bit stands, as a STATION rather than a coordinate.
 *
 * The scene owns where the furniture is, so naming the destination keeps the
 * two from drifting apart — move the crates in BitScene and Bit still digs at
 * them. The percentages themselves live in BitDock.module.css.
 *
 * Anything unlisted falls back to 'centre'.
 */
export const STATION = {
  // The routine, in the order the room is laid out: he hunts through the
  // library, drops the record on the decks beside it, then walks out to the
  // open middle to dance while it plays.
  digging: 'crates',
  asleep: 'crates',    // slumped against them, out of the way

  // Starting the track: the beat between digging it out and dancing to it.
  cueing: 'decks',

  // Anything that is ABOUT the record happens at the equipment.
  celebrate: 'decks',  // spinning out the one he just played
  sulk: 'decks',       // the deck he wrecks is the one in front of him
  rewind: 'decks',
  listening: 'decks',
  watching: 'decks',

  // Playing is the one thing he does away from the gear — the whole point of
  // keeping the right half of the room empty.
  groove: 'floor',

  catch: 'floor',      // meets a record thrown in from the queue sidebar
  locked: 'floor',
  confused: 'floor',
  idle: 'floor',
};

/** Re-roll a looping animation this often so a long idle does not go stale. */
const VARIETY_MS = 9000;

/** Fallback when a one-shot has no measured duration in the manifest. */
const DEFAULT_ONESHOT_MS = 800;

function pickDifferent(pool, last) {
  if (!pool || pool.length === 0) return null;
  if (pool.length === 1) return pool[0];
  let next = last;
  // Guaranteed to terminate: pool has >= 2 distinct entries.
  while (next === last) next = pool[Math.floor(Math.random() * pool.length)];
  return next;
}

/**
 * One beat in seconds, from the track's real BPM.
 *
 * `tempo` ships on every track payload (backend/app.py TRACK_FIELDS) and is
 * populated for 100% of playable tracks, 45-235 BPM. This is what lets the
 * groove animations beat-match: the value goes out as a CSS custom property
 * and the keyframes read it, so there is no rAF loop anywhere.
 *
 * Folded into roughly 0.42-1.4s. A 200 BPM track bobbing every 300ms reads as
 * a glitch, not dancing, so fast songs are halved onto the 2 and 4; very slow
 * ones are doubled so Bit does not appear frozen.
 */
function beatSeconds(tempo) {
  const bpm = Number(tempo);
  let b = Number.isFinite(bpm) && bpm > 0 ? 60 / bpm : 0.5;
  while (b < 0.42) b *= 2;
  while (b > 1.4) b /= 2;
  return b;
}

export default function useBitState({
  current,
  playing,
  loadingTrack,
  verifying,
  mode,
  source,
  vibe,
}) {
  /*
   * The event is {name, n} rather than a bare string. `n` is a nonce bumped
   * on every fire, so re-firing the SAME event still produces a new object
   * and re-runs the picker — a plain string would be swallowed as an
   * identical setState and the second skip in a row would do nothing.
   */
  const [event, setEvent] = useState(null);
  const [animKey, setAnimKey] = useState(null);
  const lastByState = useRef({});
  const nonce = useRef(0);

  const base = useMemo(() => {
    if (!current) return 'asleep';
    // Order matters: these are checked most-specific first. A track that is
    // loading is also "not playing", and would otherwise fall through to idle.
    if (loadingTrack) return 'digging';
    if (verifying) return 'listening';
    if (mode === 'video') return 'watching';
    // No local audio and no Spotify stream — nothing can play at all.
    if (isMetadataOnly(current) && !source) return 'locked';
    return playing ? 'groove' : 'idle';
  }, [current, loadingTrack, verifying, mode, source, playing]);

  const state = event ? event.name : base;

  // Choose an animation whenever the state changes.
  useEffect(() => {
    const pool = ANIMATIONS[state];
    if (!pool || pool.length === 0) {
      setAnimKey(null);
      return;
    }

    /*
     * Groove narrows before it rolls.
     *
     * The mood band picks the POOL — Bit sways through `chill` and headbangs
     * through `beast`, because throwing that away would discard the one thing
     * this app is about — and then a move is chosen at random within it, the
     * same as every other state. Band first, dice second.
     *
     * Speed is not involved: --beat already carries the track's real BPM, so
     * tempo and energy stay independent. A chill song and a beast song at the
     * same BPM move at the same rate and look nothing alike, which is the
     * point.
     *
     * Falls back to the flat list if the band map is missing or empty, so a
     * manifest that has not caught up yet degrades to the old behaviour
     * instead of leaving Bit standing still.
     */
    if (state === 'groove') {
      const band = moodFor(vibe).name;
      const byBand = manifest.GROOVE_BY_BAND && manifest.GROOVE_BY_BAND[band];
      const bandPool = byBand && byBand.length ? byBand : pool;
      const next = pickDifferent(bandPool, lastByState.current[`groove:${band}`]);
      lastByState.current[`groove:${band}`] = next;
      setAnimKey(next);
      return;
    }

    const next = pickDifferent(pool, lastByState.current[state]);
    lastByState.current[state] = next;
    setAnimKey(next);
    // event?.n is in the deps so a repeat of the same event re-picks.
  }, [state, vibe, event && event.n]);

  // A one-shot returns to the base state once its clip is done. Length comes
  // from the manifest rather than a guess, so the hand-off is seamless.
  useEffect(() => {
    if (!event || !animKey) return undefined;
    const meta = META[animKey];
    if (meta && meta.loop) return undefined; // not a one-shot after all
    const ms = (meta && meta.ms) || DEFAULT_ONESHOT_MS;
    const t = setTimeout(() => setEvent(null), ms);
    return () => clearTimeout(t);
  }, [event, animKey]);

  /*
   * Looping states re-roll periodically so a long stretch does not look
   * frozen. Groove is included now that each band has a pool of its own —
   * it used to be excluded because the band pinned it to exactly one move.
   */
  useEffect(() => {
    if (event) return undefined;
    const band = state === 'groove' ? moodFor(vibe).name : null;
    const byBand = band && manifest.GROOVE_BY_BAND && manifest.GROOVE_BY_BAND[band];
    const pool = byBand && byBand.length ? byBand : ANIMATIONS[state];
    if (!pool || pool.length < 2) return undefined;
    const slot = band ? `groove:${band}` : state;
    const id = setInterval(() => {
      setAnimKey((prev) => {
        const next = pickDifferent(pool, prev);
        lastByState.current[slot] = next;
        return next;
      });
    }, VARIETY_MS);
    return () => clearInterval(id);
  }, [state, event, vibe]);

  /**
   * Trigger a one-shot reaction. Ignored if the state has no animations.
   *
   * This used to clear the event and re-set it inside requestAnimationFrame
   * to force a restart. That silently broke whenever the tab was not
   * painting: rAF callbacks do not run in a background tab, so the second
   * setState never happened and Bit ignored every skip while you were on
   * another tab. The nonce does the same job with no dependency on paint.
   */
  const fire = useCallback((name) => {
    if (!ANIMATIONS[name] || ANIMATIONS[name].length === 0) return;
    nonce.current += 1;
    setEvent({ name, n: nonce.current });
  }, []);


  /*
   * A new track starting is the cue to play it.
   *
   * Keyed on the track ID rather than the `current` object: loadTrack hands
   * back a fresh object on things like a Spotify-to-preview fallback, and Bit
   * must not re-cue a record that is already spinning.
   *
   * Declared AFTER fire() because fire is a const — referencing it from an
   * effect above would read it before initialisation.
   */
  const lastTrackId = useRef(null);
  useEffect(() => {
    const id = current ? current.id : null;
    if (id == null || id === lastTrackId.current) {
      lastTrackId.current = id;
      return;
    }
    lastTrackId.current = id;
    fire('cueing');
  }, [current, fire]);

  const beat = useMemo(() => beatSeconds(current && current.tempo), [current]);
  const prop = SUGGESTED_PROP[state] || null;
  const station = STATION[state] || 'floor';

  return { state, animKey, prop, beat, station, fire };
}
