/**
 * Library membership (the heart), for any track the app holds.
 *
 * A module store rather than React state, because the same song reaches the
 * screen as many separate objects — the DJ's pick, a search hit, a recent
 * tile replayed — each carrying the `in_library` it was fetched with. A heart
 * toggled on one must read the same on every other, including copies fetched
 * before the change, so what this session learned or wrote wins over a
 * payload's value. One Map, keyed by apiKey(track), read through
 * useSyncExternalStore.
 *
 * Where a payload has no `in_library` (a backend that only offers the batch
 * endpoint), keys are collected for one tick and asked of
 * GET /api/me/library/contains in one call, once per key per session.
 *
 * Writes are optimistic: the Map flips at once, the request follows, and a
 * failure puts back the value from before — unless a later toggle of the same
 * key has happened since, whose own request then decides. Requests for one
 * key run in order, so the server sees the toggles in the order they were
 * made.
 */
import { libraryAdd, libraryContains, libraryRemove } from '../../lib/api';
import { onTokenChange } from '../../lib/session';
import { apiKey } from '../../lib/vibe';

const known = new Map(); // String(key) -> boolean
const asked = new Set(); // keys already sent to /contains
const seq = new Map(); // key -> number of the latest write
const chains = new Map(); // key -> promise of the latest request
const listeners = new Set();
let waiting = new Map(); // key -> raw apiKey, for the next /contains batch
let timer = null;

const emit = () => listeners.forEach((fn) => fn());

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** The Map key for a track, or null when it has neither id nor spotify_id. */
export function libraryKey(track) {
  if (!track) return null;
  const k = apiKey(track);
  return k === '' || k == null || Number.isNaN(k) ? null : String(k);
}

/** true / false, or null while unknown. */
export function inLibrary(track) {
  const key = libraryKey(track);
  if (key == null) return null;
  if (known.has(key)) return known.get(key);
  return typeof track.in_library === 'boolean' ? track.in_library : null;
}

/** Accepts {key: bool}, {contains: {key: bool}} or [bool] in the asked order. */
function readContains(body, keys) {
  if (Array.isArray(body)) return new Map(keys.map((k, i) => [k, body[i] === true]));
  const map = body && typeof body.contains === 'object' ? body.contains : body;
  if (!map || typeof map !== 'object') return new Map();
  return new Map(keys.filter((k) => typeof map[k] === 'boolean').map((k) => [k, map[k]]));
}

async function flush() {
  timer = null;
  const batch = waiting;
  waiting = new Map();
  const keys = [...batch.keys()];
  if (!keys.length) return;
  let answer;
  try {
    answer = readContains(await libraryContains([...batch.values()]), keys);
  } catch {
    // A guest, or a backend without the route: the heart reads "not in your
    // library", and clicking it still adds. Not worth a toast.
    return;
  }
  let changed = false;
  for (const [k, v] of answer) {
    if (seq.has(k)) continue; // written since it was asked: the write wins
    known.set(k, v);
    changed = true;
  }
  if (changed) emit();
}

/** Queue a track whose payload had no `in_library` for the next batch. */
export function askMembership(track) {
  const key = libraryKey(track);
  if (key == null || asked.has(key) || known.has(key) || typeof track.in_library === 'boolean') return;
  asked.add(key);
  waiting.set(key, apiKey(track));
  if (!timer) timer = setTimeout(flush, 0);
}

/**
 * Add (want = true) or remove the track. Resolves to 'ok', 'failed' (rolled
 * back; the caller says so), or 'stale' (failed, but a later toggle of the
 * same track has taken over, so there is nothing to roll back or report).
 */
export function setInLibrary(track, want) {
  const key = libraryKey(track);
  if (key == null) return Promise.resolve('failed');
  const before = inLibrary(track) === true;
  const n = (seq.get(key) || 0) + 1;
  seq.set(key, n);
  known.set(key, want);
  emit();
  const raw = apiKey(track);
  const run = () => (want ? libraryAdd(raw) : libraryRemove(raw));
  const p = (chains.get(key) || Promise.resolve()).then(run, run);
  chains.set(key, p);
  return p.then(
    () => (seq.get(key) === n ? 'ok' : 'stale'),
    () => {
      if (seq.get(key) !== n) return 'stale';
      known.set(key, before);
      emit();
      return 'failed';
    },
  );
}

/** Forget everything — another account's library is a different library. */
export function resetLibraryStore() {
  known.clear();
  asked.clear();
  seq.clear();
  chains.clear();
  waiting = new Map();
  if (timer) clearTimeout(timer);
  timer = null;
  emit();
}

onTokenChange(resetLibraryStore);
