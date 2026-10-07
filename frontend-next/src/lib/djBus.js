/**
 * One-way signal from "the user did a DJ-worthy thing from somewhere else"
 * to useDj's event sink.
 *
 * useDj owns the taste map in localStorage and in React state. Letting any
 * other component import useDj would double-instantiate that state (two
 * copies of the map, each writing over the other). Lifting the map into
 * PlayerContext would re-render the entire player tree on every play/skip
 * for a side-effect that only the sidebar's rec query cares about.
 *
 * So instead, emit through this bus. useDj subscribes once and calls its
 * own push(). Same pattern as lib/mascotBus.js.
 *
 * Current emitter: features/search/SearchBar.jsx fires 'searched' when the
 * user picks a result. Anything else that represents explicit "I want this
 * vibe" intent from outside the queue sidebar belongs here too.
 */

const listeners = new Set();

/**
 * `evt` is the full event payload: { track_id, action, played_ratio, ts }.
 * action matches the vocabulary verdictFor() understands ('searched',
 * 'queued', 'completed', 'next', 'skipped').
 */
export function emitDj(evt) {
  listeners.forEach((fn) => {
    try {
      fn(evt);
    } catch {
      /* a broken listener must never take down the thing that emitted */
    }
  });
}

export function onDj(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
