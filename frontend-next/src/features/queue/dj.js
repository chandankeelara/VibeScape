/**
 * DJ mode — pure logic.
 *
 * The client keeps an append-only LOG of what happened — {id, action,
 * played_ratio, ts}, oldest first — and sends the newest DJ_MAX_SENT_EVENTS
 * of it with every DJ fetch. It holds no weights: the backend replays the log
 * into a query vector (backend/dj_replay.py), so every tuning knob lives in
 * one place and the offline replay harness runs the same code.
 *
 * Nothing is merged and nothing expires by the clock. A track played twice is
 * in the log twice; the replay is a bounded moving average, so repeats cannot
 * run away. The log survives reloads and long gaps on purpose — coming back
 * after a day resumes the vibe you tuned, and one search or a short run of
 * skips redirects it. Only the DJ_MAX_EVENTS cap ever drops an entry.
 *
 * The user's queue is NEVER written to by DJ mode — that promise is load
 * bearing and is why this module returns picks instead of enqueueing them.
 *
 * Everything here is a pure function or a localStorage read/write; React
 * state lives in useDj.js.
 */

import * as api from '../../lib/api';
import { apiKey } from '../../lib/vibe';

export const DJ_STORAGE_KEY = 'vibescape.sessionEvents';
export const DJ_TOGGLE_KEY = 'vibescape.djEnabled';

/** Entries kept in localStorage. The backend reads at most 30 of them. */
export const DJ_MAX_EVENTS = 50;

/**
 * Entries POSTed per fetch. Must not exceed the backend's MAX_EVENTS (30) —
 * it keeps the newest 30 and drops the rest. Each costs one embedding load.
 * In the replay an event 15 back weighs ~0.15 * 0.85^15 ≈ 0.01 of S at the
 * slowest learning rate, so 30 is well past where older events matter.
 */
export const DJ_MAX_SENT_EVENTS = 30;

/**
 * How far back the restored log re-seeds the in-memory seen-set on mount.
 * The log itself is kept indefinitely, but exclusion is per session:
 * PlayerContext's seen-set is deliberately not persisted, so a reload should
 * restore the session in progress, not ban last week's tracks.
 */
export const DJ_SEEN_RESEED_HOURS = 3;

/**
 * Exclude list cap. Must equal SEEN_MAX in PlayerContext.jsx — a smaller
 * value here would silently drop entries the seen-set still remembers,
 * making tracks eligible again while the frontend believed they were excluded.
 *
 * 200 is the backend's own _DJ_MAX_EXCLUDE_IDS, which truncates anything
 * longer, and backend/app.py inlines these as SQL literals in a `NOT IN (...)`
 * clause. At ~7 bytes per id that is ~1.4 KB on the wire.
 */
export const DJ_MAX_EXCLUDES = 200;

/** Played ratio at or above which a transition counts as a full listen. */
export const DJ_COMPLETED_THRESHOLD = 0.85;

/**
 * Actions the backend replay understands. Anything else is not logged.
 *
 *   searched   search result played — "this, now"
 *   queued     added to the queue
 *   picked     played straight off a list: a rec, the queue, the recent trail
 *   completed  played to DJ_COMPLETED_THRESHOLD or further
 *   next       moved on between 45% and 85%
 *   skipped    moved on before 45%
 *
 * A track left because the user searched or picked another is classified by
 * its played ratio like any other — measured offline 2026-10-10, treating
 * those as neutral instead made no difference (24 cases, AUC 0.828 vs 0.830).
 */
export const DJ_ACTIONS = ['searched', 'queued', 'picked', 'completed', 'next', 'skipped'];
const KNOWN = new Set(DJ_ACTIONS);

/* ------------------------------------------------------------ log storage */

/**
 * Schema version + origin stamp.
 *
 * v4: append-only log, {stamp, events: [{id, action, played_ratio, ts}]}.
 * v3 was a per-track verdict map and is MIGRATED (each entry becomes one
 * event at its last-touched time) so existing tuning survives the upgrade.
 * v1 / v2 are dropped.
 *
 * Internal track ids are only valid against the backend that issued them
 * (local SQLite ≠ prod Turso), so the stamp pins the origin too.
 */
const DJ_SCHEMA = 4;
const stamp = (v = DJ_SCHEMA) => `${v}:${window.location.origin}`;

/** v3 {id -> {action, verdict, weight, ts}} -> v4 events, oldest first. */
function migrateV3(tracks) {
  const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
  return Object.values(tracks || {})
    .filter((e) => e && e.id != null && KNOWN.has(e.action) && Number.isFinite(e.ts))
    .sort((a, b) => a.ts - b.ts)
    .map((e) => {
      // v3 kept a weight, not the ratio. Recover a ratio that classifies
      // back to the same action.
      let ratio = null;
      if (e.action === 'completed') ratio = 1;
      else if (e.action === 'next') ratio = clamp(e.weight, 0.45, 0.84);
      else if (e.action === 'skipped') ratio = clamp(1 - e.weight, 0, 0.44);
      return { id: e.id, action: e.action, played_ratio: ratio, ts: e.ts };
    })
    .slice(-DJ_MAX_EVENTS);
}

export function loadEvents() {
  try {
    const raw = localStorage.getItem(DJ_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!parsed || Array.isArray(parsed)) return [];
    if (parsed.stamp === stamp(3)) return migrateV3(parsed.tracks);
    if (parsed.stamp !== stamp() || !Array.isArray(parsed.events)) return [];
    return parsed.events.slice(-DJ_MAX_EVENTS);
  } catch {
    return [];
  }
}

export function persistEvents(events) {
  try {
    localStorage.setItem(DJ_STORAGE_KEY, JSON.stringify({ stamp: stamp(), events }));
  } catch {
    /* private mode / quota — the log is a nicety, not a requirement */
  }
}

export function loadEnabled() {
  try {
    return localStorage.getItem(DJ_TOGGLE_KEY) === '1';
  } catch {
    return false;
  }
}

export function persistEnabled(on) {
  try {
    localStorage.setItem(DJ_TOGGLE_KEY, on ? '1' : '0');
  } catch {
    /* ignore */
  }
}

/* ------------------------------------------------------------ classify */

/**
 * Classify how a track ended, given how much of it was played. Preserved
 * from the pre-rewrite API because the mascot bus and useDj.js both call it.
 *
 * `natural` (track ran to its own end) is passed through as a hard "completed"
 * because a player can report a ratio slightly under 1.0 on the final tick.
 */
export function classifyTransition(ratio, { natural = false } = {}) {
  const r = Number.isFinite(ratio) ? ratio : 0;
  if (natural || r >= DJ_COMPLETED_THRESHOLD) return 'completed';
  if (r >= 0.45) return 'next';
  return 'skipped';
}

/**
 * Append one event, returning a new array — or the SAME array when the event
 * is not loggable, so callers can short-circuit on identity (mascot etc).
 *
 * `evt` is the bus shape {track_id, action, played_ratio, ts}. The id keeps
 * its native type: a number short-circuits backend resolution, a string
 * (un-ingested Spotify result) still batch-resolves.
 */
export function appendEvent(events, evt) {
  if (!evt || evt.track_id == null || evt.track_id === '' || !KNOWN.has(evt.action)) {
    return events;
  }
  const r = evt.played_ratio;
  const entry = {
    id: evt.track_id,
    action: evt.action,
    played_ratio: typeof r === 'number' && Number.isFinite(r) ? Math.max(0, Math.min(1, r)) : null,
    ts: evt.ts || Date.now(),
  };
  const next = [...events, entry];
  return next.length > DJ_MAX_EVENTS ? next.slice(-DJ_MAX_EVENTS) : next;
}

/** Distinct track ids touched in the last `hours`, for re-seeding the seen-set. */
export function recentIds(events, hours = DJ_SEEN_RESEED_HOURS, now = Date.now()) {
  const cutoff = now - hours * 3_600_000;
  const ids = new Set();
  for (const e of events) if (e.ts >= cutoff) ids.add(e.id);
  return [...ids];
}

/**
 * Internal integer track ids the DJ must not pick again: the session
 * seen-set, plus whatever is currently queued.
 *
 * The seen-set is outcome-blind on purpose. A track enters it the moment it
 * is put in front of the user — played, skipped, queued, however it was
 * reached — because "don't show me this again" doesn't depend on whether they
 * liked it. What they thought of it is a separate question, answered by the
 * backend's replay of the event log.
 *
 * `queue` is unioned in even though enqueue() marks its tracks seen, because
 * that only holds at insertion: the set evicts LRU at SEEN_MAX, so a track
 * queued early and still waiting deep in a long queue can drop out of the set
 * while it is still pending. Queue first, so it survives the cap.
 *
 * These are `tracks.id` integers rather than spotify keys on purpose — the
 * backend skips per-key resolution for ints.
 */
export function excludeIds({ seen = [], queue = [] }) {
  const ids = new Set();
  const addId = (v) => {
    const n = Number(v);
    if (Number.isFinite(n)) ids.add(n);
  };
  queue.forEach((t) => { if (t && t.id != null) addId(t.id); });
  seen.forEach(addId);
  return Array.from(ids).slice(0, DJ_MAX_EXCLUDES);
}

/**
 * Signature of the log — used to coalesce redundant fetches. Changes whenever
 * an event lands (the newest ts moves even once the log is at its cap).
 */
export const eventsSignature = (events) =>
  `${events.length}:${events.length ? events[events.length - 1].ts : 0}`;

/* ----------------------------------------------------------------- fetching */

/**
 * Fetch the DJ pick list for `seed`.
 *
 * POST /api/tracks/{key}/similar with the newest DJ_MAX_SENT_EVENTS of the
 * log. The seed's own events are part of the log and DO count (a searched
 * track is the seed while it plays); the seed itself is still never returned.
 * api.js degrades to plain similarity if similarTracksDj is not wired yet.
 */
export async function fetchDjPicks(seed, { events, seen = [], queue = [], limit = 8 }) {
  const key = apiKey(seed);
  if (!key) return [];

  if (typeof api.similarTracksDj === 'function') {
    const res = await api.similarTracksDj(key, {
      mode: 'dj',
      events: events.slice(-DJ_MAX_SENT_EVENTS),
      exclude_ids: excludeIds({ seen, queue }),
      limit,
    });
    return res?.tracks || [];
  }

  const res = await api.similarTracks(key, { limit });
  return res?.tracks || [];
}

/** Plain vibe-similarity list, used whenever DJ mode is off. */
export async function fetchSimilar(seed, { limit = 8 } = {}) {
  const key = apiKey(seed);
  if (!key) return [];
  const res = await api.similarTracks(key, { limit });
  return res?.tracks || [];
}
