/**
 * DJ mode — pure logic, ported from frontend/app.js:5079-5310.
 *
 * A rolling buffer of the last 10 playback events lives in localStorage. When
 * DJ mode is ON the buffer is weighted and POSTed to
 * POST /api/tracks/{seed}/similar (mode: 'dj'), which scores the user's
 * library by session-weighted MERT-embedding cosine similarity. The result
 * replaces the plain vibe-similarity list in the recs sidebar.
 *
 * The user's queue is NEVER written to by DJ mode — that promise is load
 * bearing and is why this module returns picks instead of enqueueing them.
 *
 * Everything here is a pure function or a localStorage read/write; React state
 * lives in useDj.js. Keeping them apart is what makes the weighting logic
 * testable without mounting a component.
 */

import * as api from '../../lib/api';
import { apiKey } from '../../lib/vibe';

export const DJ_STORAGE_KEY = 'vibescape.sessionEvents';
export const DJ_TOGGLE_KEY = 'vibescape.djEnabled';
export const DJ_MAX_EVENTS = 100;

/**
 * Caps on what actually goes over the wire. The buffer is deep so the taste
 * vector has history; the payload stays small so each DJ fetch stays cheap.
 *
 * The backend loads one 768-float embedding per id sent (_load_mert_vecs_bulk),
 * so an uncapped 100-event buffer would pull ~100 vectors from Turso on every
 * pick. Sending the highest-weighted ids keeps the signal and drops the tail,
 * which contributes almost nothing once decayed.
 */
export const DJ_MAX_SENT_IDS = 20;

/**
 * Exclude list cap. queue is unbounded (enqueue just appends) while recent is
 * capped at 12, so without this a long queue produced a correspondingly long
 * `NOT IN (...)` clause — and backend/app.py:1366 inlines those ids as SQL
 * literals on the assumption the list is "bounded (~50-100)".
 *
 * 150 covers current + 12 recent + a queue + the 100-event buffer with room to
 * spare, and stays under the backend's own 200 cap.
 */
export const DJ_MAX_EXCLUDES = 150;

/**
 * No age decay: every event in the 10-slot buffer counts at full base weight.
 * Kept as a named constant (rather than deleting the exponent) because the
 * At DJ_MAX_EVENTS=100 a flat 1.0 would weight a track from 100 plays ago the
 * same as the one just skipped, so recency decay is required rather than
 * optional. 0.97^99 ~= 0.05, i.e. the oldest event still counts, barely.
 */
export const DJ_DECAY = 0.97;

/** Played ratio at or above which a transition counts as a full listen. */
export const DJ_COMPLETED_THRESHOLD = 0.85;

/* ------------------------------------------------------------ event buffer */

/**
 * Buffer schema version + origin stamp.
 *
 * Events now carry internal `tracks.id` ints so the backend skips resolution
 * entirely. Internal ids are ONLY valid against the backend that issued them —
 * local SQLite and prod Turso assign different ids, and a prod push rewrites
 * them. A buffer carried across origins would silently weight the wrong
 * tracks, so we drop it on any mismatch. The buffer is 10 slots of recent
 * behaviour; discarding it costs nothing.
 */
const DJ_SCHEMA = 2;
const stamp = () => `${DJ_SCHEMA}:${window.location.origin}`;

export function loadEvents() {
  try {
    const raw = localStorage.getItem(DJ_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    // v1 was a bare array of spotify_id-keyed events — discard it.
    if (Array.isArray(parsed)) return [];
    if (!parsed || parsed.stamp !== stamp()) return [];
    return Array.isArray(parsed.events) ? parsed.events.slice(-DJ_MAX_EVENTS) : [];
  } catch {
    return [];
  }
}

export function persistEvents(events) {
  try {
    localStorage.setItem(
      DJ_STORAGE_KEY,
      JSON.stringify({ stamp: stamp(), events: events.slice(-DJ_MAX_EVENTS) })
    );
  } catch {
    /* private mode / quota — the buffer is a nicety, not a requirement */
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

/** Append one event, trimming to the newest DJ_MAX_EVENTS. Returns a new array. */
export function appendEvent(events, evt) {
  if (!evt || evt.track_id == null || evt.track_id === '') return events;
  const next = [
    ...events,
    {
      // Native type on purpose: a number short-circuits backend resolution,
      // a string (un-ingested Spotify result) still batch-resolves.
      track_id: evt.track_id,
      action: evt.action,
      played_ratio: typeof evt.played_ratio === 'number' ? evt.played_ratio : null,
      ts: evt.ts || Date.now(),
    },
  ];
  return next.slice(-DJ_MAX_EVENTS);
}

/**
 * Classify how a track ended, given how much of it was played.
 *
 * `natural` (track ran to its own end) is passed through as a hard "completed"
 * because a player can report a ratio slightly under 1.0 on the final tick.
 * Between 0.45 and the completion threshold the signal is genuinely ambiguous,
 * so it is recorded as 'next' and the weighting table decides what to do with
 * it — that is where a half-listen earns a small positive instead of a
 * negative it hasn't earned.
 */
export function classifyTransition(ratio, { natural = false } = {}) {
  const r = Number.isFinite(ratio) ? ratio : 0;
  if (natural || r >= DJ_COMPLETED_THRESHOLD) return 'completed';
  if (r >= 0.45) return 'next';
  return 'skipped';
}

/* --------------------------------------------------------------- weighting */

/**
 * Collapse the event buffer into {positives, negatives}, each an array of
 * {id, weight} ready to POST.
 *
 * Base weights, ported verbatim:
 *   queued      1.2 positive  — an explicit act of taste, the strongest signal
 *   completed   0.8 positive
 *   next        0.3 positive, but only when >50% was played; otherwise ignored
 *   skipped     0.8 negative under 15% played, 0.4 negative under 45%,
 *               ignored above that (a 45-85% "skip" is not a rejection)
 *
 * Each weight is multiplied by DJ_DECAY^age, where age counts backwards in
 * events (0 = most recent), so re-tuning DJ_DECAY recency-biases the vector.
 *
 * A track can legitimately land in both piles across a session (skipped once,
 * queued later). It is assigned to whichever pile has the larger total, and
 * sent with that pile's own summed weight.
 */
export function buildWeights(events) {
  const n = events.length;
  const pos = new Map();
  const neg = new Map();

  for (let i = 0; i < n; i++) {
    const e = events[i];
    const age = n - 1 - i; // 0 = most recent
    const decay = Math.pow(DJ_DECAY, age);
    const ratio = typeof e.played_ratio === 'number' ? e.played_ratio : 0;

    let base = 0;
    let bucket = null;

    if (e.action === 'completed') {
      base = 0.8;
      bucket = pos;
    } else if (e.action === 'queued') {
      base = 1.2;
      bucket = pos;
    } else if (e.action === 'next') {
      if (ratio > 0.5) {
        base = 0.3;
        bucket = pos;
      }
    } else if (e.action === 'skipped') {
      if (ratio < 0.15) {
        base = 0.8;
        bucket = neg;
      } else if (ratio < 0.45) {
        base = 0.4;
        bucket = neg;
      }
    }

    if (!bucket || !base) continue;
    bucket.set(e.track_id, (bucket.get(e.track_id) || 0) + base * decay);
  }

  const positives = [];
  const negatives = [];
  for (const id of new Set([...pos.keys(), ...neg.keys()])) {
    const p = pos.get(id) || 0;
    const g = neg.get(id) || 0;
    if (p >= g && p > 0) positives.push({ id, weight: Number(p.toFixed(4)) });
    else if (g > 0) negatives.push({ id, weight: Number(g.toFixed(4)) });
  }
  // Strongest signal first, then trim. Sorting before the cap matters: the
  // tail of a decayed 100-event buffer is near-zero weight, so dropping it
  // costs almost nothing while bounding the request.
  const strongest = (arr) =>
    arr.sort((a, b) => b.weight - a.weight).slice(0, DJ_MAX_SENT_IDS);

  return { positives: strongest(positives), negatives: strongest(negatives) };
}

/**
 * Internal integer track ids the DJ must not pick again: everything queued,
 * recently played, or now playing.
 *
 * These are `tracks.id` integers rather than spotify keys on purpose — the
 * backend skips per-key resolution for ints, which took a ~50-entry exclude
 * list from ~750ms of DB round-trips down to nothing.
 */
export function excludeIds({ queue = [], recent = [], current = null, events = [], seen = [] }) {
  const ids = new Set();
  const addId = (v) => {
    const n = Number(v);
    // Ints only. A string here is a spotify_id from a track that isn't in the
    // library yet, which the backend would have to resolve — and an
    // un-ingested track can't be a candidate anyway.
    if (Number.isFinite(n)) ids.add(n);
  };
  const add = (t) => { if (t && t.id != null) addId(t.id); };
  // Order matters once capped — the entries most redundant to recommend must
  // survive the trim.
  //   current : playing right now
  //   recent  : pushRecent appends, so reverse gives most-recently-played first
  //   queue   : NATURAL order — queue[0] is what plays next (next() takes the
  //             head), so the front is the most important to exclude. Reversing
  //             this would keep the tail and drop the tracks about to play.
  add(current);
  [...recent].reverse().forEach(add);
  queue.forEach(add);
  // Everything in the event buffer has already been heard this session.
  // Without this, taste memory (100 events) far outran repeat memory (12
  // recents): a track skipped 13 plays ago stayed a candidate, down-weighted
  // by pos - 0.4*neg but never actually forbidden. That is the "I skipped
  // this, why is it back?" case. Newest first so the cap keeps what matters.
  for (let i = events.length - 1; i >= 0; i--) addId(events[i].track_id);
  // The session seen-set: everything played or queued, however it was
  // reached — search, recs, queue, DJ, the trail. PlayerContext owns it and
  // hands it over newest-first, so the cap keeps the most recent.
  seen.forEach(addId);

  return Array.from(ids).slice(0, DJ_MAX_EXCLUDES);
}

/** Signature of the buffer's current state — used to coalesce redundant fetches. */
export const bufferSignature = (events) =>
  `${events.length}:${events.length ? events[events.length - 1].ts : 0}`;

/* ----------------------------------------------------------------- fetching */

/**
 * Fetch the DJ pick list for `seed`.
 *
 * Wants POST /api/tracks/{key}/similar with the session weights. `api.js` is
 * owned by another agent and currently only wraps the GET variant, so this
 * degrades to plain similarity until `similarTracksDj` is added there (see the
 * note in the port report). The shape of the response is identical either way.
 */
export async function fetchDjPicks(seed, { queue, recent, current, events, seen = [], limit = 8 }) {
  const key = apiKey(seed);
  if (!key) return [];

  if (typeof api.similarTracksDj === 'function') {
    const { positives, negatives } = buildWeights(events);
    const res = await api.similarTracksDj(key, {
      mode: 'dj',
      positive_ids: positives,
      negative_ids: negatives,
      exclude_ids: excludeIds({ queue, recent, current, events, seen }),
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
