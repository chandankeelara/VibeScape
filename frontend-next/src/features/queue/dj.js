/**
 * DJ mode — pure logic.
 *
 * The client holds one verdict per unique track touched this session, not a
 * log of events. On every playback transition / queue-add, the latest verdict
 * for that track REPLACES the stored entry — nothing accumulates. Repeated
 * plays of the same track no longer compound into runaway weight; whether a
 * track is a positive or negative signal is set by what happened to it most
 * recently.
 *
 * Decay is by elapsed time, not buffer position, so an idle hour actually
 * ages the signal instead of being masked by the next event's reindex.
 * Entries past DJ_MAX_AGE_HOURS are dropped outright — the frontend buffer
 * is session-intent only; cross-session memory is the backend's job.
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

/**
 * Hard age cutoff for stored verdicts. Entries older than this are dropped
 * on next upsert and on load, regardless of weight.
 *
 * The frontend buffer represents **this session's intent only** — the backend
 * carries cross-session memory via its own 72h/168h recency rerank
 * (`_recency_penalty`). A weight-based eviction policy let a strongly-weighted
 * early signal (e.g. a `queued`=1.2) squat in the top-K sent slice for hours,
 * because even heavily decayed it still beat newer small signals. A hard
 * window makes "rolling" literal.
 */
export const DJ_MAX_AGE_HOURS = 1;

/**
 * Safety cap on map size. Routine pressure is bounded by DJ_MAX_AGE_HOURS;
 * this is a floor against pathological event storms within the window.
 */
export const DJ_MAX_TRACKS = 100;

/**
 * Cap on ids actually POSTed per DJ fetch (positives AND negatives, each
 * capped separately — so up to 2*DJ_MAX_SENT_IDS go over the wire).
 *
 * Each sent id costs one 768-float embedding load on the backend
 * (_load_mert_vecs_bulk). With the per-track-verdict map we no longer need
 * the old slack for accumulated noise, so 15 is tighter than the old 20
 * while still carrying the strongest-weighted slice of the signal.
 */
export const DJ_MAX_SENT_IDS = 15;

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

/**
 * Per-hour recency decay for stored verdicts. 0.6 gives a signal half-life
 * of ~90 minutes (0.6^1.5 ≈ 0.465), which matches the practical span of the
 * old positional 0.97^k over a typical session (events ~2-3 min apart ×
 * ~20-event half-life ≈ 45-90 min). An idle user's older verdicts fade on
 * their own instead of being frozen in place until the next event reindexes
 * the buffer.
 */
export const DJ_DECAY_PER_HOUR = 0.6;

/**
 * Below this decayed weight an entry contributes nothing meaningful to the
 * query vector. Filter it out before sorting to keep the top-K honest.
 */
const DJ_WEIGHT_FLOOR = 0.001;

/** Played ratio at or above which a transition counts as a full listen. */
export const DJ_COMPLETED_THRESHOLD = 0.85;

/* --------------------------------------------------------- verdict storage */

/**
 * Buffer schema version + origin stamp.
 *
 * v3: per-track verdict map, {stamp, tracks: {id -> {verdict, weight, ts}}}.
 * Earlier schemas were arrays or {stamp, events: [...]} and are DROPPED on
 * load — the buffer is a cheap recent-behaviour signal and discarding it
 * costs nothing.
 *
 * Internal track ids are only valid against the backend that issued them
 * (local SQLite ≠ prod Turso), so the stamp pins the origin too.
 */
const DJ_SCHEMA = 3;
const stamp = () => `${DJ_SCHEMA}:${window.location.origin}`;

export function loadTracks() {
  try {
    const raw = localStorage.getItem(DJ_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    // v1 was a bare array; v2 was {stamp, events: [...]}; both discarded.
    if (!parsed || Array.isArray(parsed) || parsed.stamp !== stamp()) return {};
    const tracks = parsed.tracks && typeof parsed.tracks === 'object' ? parsed.tracks : {};
    // Drop entries past the age window so a reload after a long idle gap
    // starts clean rather than carrying stale verdicts.
    return pruneByAge(tracks, Date.now());
  } catch {
    return {};
  }
}

export function persistTracks(tracks) {
  try {
    localStorage.setItem(
      DJ_STORAGE_KEY,
      JSON.stringify({ stamp: stamp(), tracks })
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

/**
 * Classify an action+ratio into a verdict for the taste map.
 *
 * Sign is set by the action; magnitude is set by the played ratio. The
 * action label survives so the mascot bus can still differentiate (a skip
 * animates differently than a next-click).
 *
 *   searched    +0.8 — explicit intent, no outcome yet
 *   queued      +1.2 — explicit commit, strongest hands-on signal
 *   completed   +max(r, 0.85) — natural end can report r≈0.998 or lower
 *   next        +r — classifyTransition guarantees r in [0.45, 0.85]
 *   skipped     -(1 - r) — classifyTransition guarantees r in [0, 0.45),
 *               so magnitude lives in (0.55, 1.0]: earlier bail = stronger
 *
 * Returns null only for an unknown action.
 */
function verdictFor(action, ratio) {
  const r = typeof ratio === 'number' ? Math.max(0, Math.min(1, ratio)) : 0;
  if (action === 'searched')  return { verdict: 'positive', weight: 0.8 };
  if (action === 'queued')    return { verdict: 'positive', weight: 1.2 };
  if (action === 'completed') return { verdict: 'positive', weight: Math.max(r, 0.85) };
  if (action === 'next')      return { verdict: 'positive', weight: r };
  if (action === 'skipped')   return { verdict: 'negative', weight: 1 - r };
  return null;
}

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
 * Upsert one event into the per-track verdict map, returning a new object.
 *
 * ACCUMULATE on repeat: when the track is already in the map, the existing
 * contribution is first decayed by elapsed time and then signed-summed with
 * the new event's weight. A search followed by a completion stacks (0.8 +
 * 0.95 → 1.75 positive). A skip followed by a later completion partially
 * cancels. A play then another play 3h later goes to ~1.16, not 2× — the
 * decay bounds runaway accumulation that broke the old positional scheme.
 *
 * The stored ts always advances to the newest event, so decay in
 * buildWeights is measured from the latest interaction.
 *
 * Returns the same object reference when the event carries no verdict, so
 * callers can short-circuit on identity to skip side-effects (mascot etc).
 */
export function upsertTrack(tracks, evt) {
  if (!evt || evt.track_id == null || evt.track_id === '') return tracks;
  const v = verdictFor(evt.action, evt.played_ratio);
  if (!v) return tracks;
  const key = String(evt.track_id);
  const now = evt.ts || Date.now();
  const existing = tracks[key];

  const new_signed = v.verdict === 'positive' ? v.weight : -v.weight;
  const ageHours = existing ? Math.max(0, (now - existing.ts) / 3_600_000) : Infinity;
  let combined;
  // An existing entry past the age window counts as not there — don't
  // resurrect a stale verdict by signed-summing it into the new one.
  if (existing && ageHours <= DJ_MAX_AGE_HOURS) {
    const existing_decayed = existing.weight * Math.pow(DJ_DECAY_PER_HOUR, ageHours);
    const existing_signed = existing.verdict === 'positive' ? existing_decayed : -existing_decayed;
    combined = existing_signed + new_signed;
  } else {
    combined = new_signed;
  }

  const base = pruneByAge(tracks, now);
  const next = {
    ...base,
    [key]: {
      // Native type preserved: a number short-circuits backend resolution,
      // a string (un-ingested Spotify result) still batch-resolves.
      id: evt.track_id,
      // Latest action label wins — matters for the mascot and debugging.
      // The combined weight is what the ranker sees.
      action: evt.action,
      verdict: combined >= 0 ? 'positive' : 'negative',
      weight: Math.abs(combined),
      ts: now,
    },
  };
  return Object.keys(next).length > DJ_MAX_TRACKS ? pruneByRecency(next) : next;
}

/**
 * Drop entries whose age exceeds DJ_MAX_AGE_HOURS. Primary eviction.
 */
function pruneByAge(tracks, now) {
  const cutoff = now - DJ_MAX_AGE_HOURS * 3_600_000;
  const kept = {};
  for (const [k, e] of Object.entries(tracks)) {
    if (e.ts >= cutoff) kept[k] = e;
  }
  return kept;
}

/**
 * Safety trim when the map blows past DJ_MAX_TRACKS inside the window.
 * Keeps the most recent entries by ts — a true rolling window has no reason
 * to privilege one weight class over another when it must drop something.
 */
function pruneByRecency(tracks) {
  const entries = Object.values(tracks).sort((a, b) => b.ts - a.ts);
  const kept = {};
  for (let i = 0; i < Math.min(entries.length, DJ_MAX_TRACKS); i++) {
    kept[String(entries[i].id)] = entries[i];
  }
  return kept;
}

/* --------------------------------------------------------------- weighting */

/**
 * Collapse the verdict map into {positives, negatives}, each an array of
 * {id, weight} ready to POST. Weights are decayed by elapsed time.
 *
 * A track lives in exactly one bucket at a time (its latest verdict's) — the
 * old "same track on both sides, bigger pile wins" dance is gone because
 * upsertTrack already replaced the losing side.
 */
export function buildWeights(tracks) {
  const now = Date.now();
  const positives = [];
  const negatives = [];
  for (const e of Object.values(tracks)) {
    const ageHours = Math.max(0, (now - e.ts) / 3_600_000);
    const decayed = e.weight * Math.pow(DJ_DECAY_PER_HOUR, ageHours);
    if (decayed < DJ_WEIGHT_FLOOR) continue;
    const entry = { id: e.id, weight: Number(decayed.toFixed(4)) };
    (e.verdict === 'positive' ? positives : negatives).push(entry);
  }
  const strongest = (arr) =>
    arr.sort((a, b) => b.weight - a.weight).slice(0, DJ_MAX_SENT_IDS);
  return { positives: strongest(positives), negatives: strongest(negatives) };
}

/**
 * Internal integer track ids the DJ must not pick again: the session
 * seen-set, plus whatever is currently queued.
 *
 * The seen-set is outcome-blind on purpose. A track enters it the moment it
 * is put in front of the user — played, skipped, queued, however it was
 * reached — because "don't show me this again" doesn't depend on whether they
 * liked it. What they thought of it is a separate question, answered by
 * buildWeights().
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
 * Signature of the current verdict map — used to coalesce redundant fetches.
 * Changes whenever any new verdict lands (count or latest-ts moves).
 */
export const tracksSignature = (tracks) => {
  const vals = Object.values(tracks);
  const latest = vals.reduce((max, e) => (e.ts > max ? e.ts : max), 0);
  return `${vals.length}:${latest}`;
};

/* ----------------------------------------------------------------- fetching */

/**
 * Fetch the DJ pick list for `seed`.
 *
 * Wants POST /api/tracks/{key}/similar with the session weights. api.js
 * degrades to plain similarity if similarTracksDj is not wired yet.
 */
export async function fetchDjPicks(seed, { tracks, seen = [], queue = [], limit = 8 }) {
  const key = apiKey(seed);
  if (!key) return [];

  if (typeof api.similarTracksDj === 'function') {
    const { positives, negatives } = buildWeights(tracks);
    const res = await api.similarTracksDj(key, {
      mode: 'dj',
      positive_ids: positives,
      negative_ids: negatives,
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
