/**
 * A one-way signal from "something happened" to the mascot.
 *
 * Bit reacts to DJ events — you finished a track, skipped one early, queued
 * one — but those live in useDj's internal buffer inside the queue sidebar
 * and are never exposed as a reactive value. Routing them through React would
 * mean either lifting the whole buffer into PlayerContext (it changes on every
 * play/skip, so the entire player tree would re-render for a cosmetic
 * animation) or threading a callback through three components that have no
 * other reason to know the mascot exists.
 *
 * A module singleton is also what the media layer already does — player.js and
 * spotify.js both expose subscribe() the same way.
 *
 * Lives in lib/ rather than in features/player/bit/ so that features/queue can
 * emit without importing from another feature. Emitters know nothing about who
 * is listening, and nothing listening is required for the app to work: if the
 * mascot is not mounted, these calls are no-ops.
 */

const listeners = new Set();

/**
 * Announce an event. `name` is the raw DJ action ('completed' | 'skipped' |
 * 'next' | 'queued') or a player action ('rewind'). Mapping those onto Bit's
 * states is the mascot's business, not the caller's — the queue should not
 * have to know that a skip makes a robot sulk.
 */
export function emitMascot(name, detail) {
  listeners.forEach((fn) => {
    try {
      fn(name, detail);
    } catch {
      /* a broken listener must never take down the thing that emitted */
    }
  });
}

/** Subscribe. Returns an unsubscribe function. */
export function onMascot(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
