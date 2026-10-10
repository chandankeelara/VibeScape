/**
 * One-way signal from "the user did a DJ-worthy thing from somewhere else"
 * to useDj's event sink.
 *
 * useDj owns the event log in localStorage and in React state. Letting any
 * other component import useDj would double-instantiate that state (two
 * copies of the log, each writing over the other). Lifting the log into
 * PlayerContext would re-render the entire player tree on every play/skip
 * for a side-effect that only the sidebar's rec query cares about.
 *
 * So instead, emit through this bus. useDj subscribes once and calls its
 * own push(). Same pattern as lib/mascotBus.js.
 *
 * Current emitters: features/search/SearchBar.jsx fires 'searched' when the
 * user plays a result and 'queued' for "+ queue"; features/player/
 * RecentTrail.jsx fires 'picked'. Anything else that represents explicit
 * "I want this" intent from outside the queue sidebar belongs here too.
 */

const listeners = new Set();

/**
 * `evt` is the full event payload: { track_id, action, played_ratio, ts }.
 * action is one of DJ_ACTIONS in features/queue/dj.js ('searched', 'queued',
 * 'picked', 'completed', 'next', 'skipped'); anything else is not logged.
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
