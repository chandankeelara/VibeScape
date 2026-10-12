/**
 * Output volume + mute, shared by every playback backend.
 *
 * A module singleton like the rest of media/: one value for the whole page,
 * persisted, and pushed to whichever backends exist. Backends register a
 * SINK (registerSink) instead of this module importing them — player.js,
 * spotify.js and youtube.js already import each other's neighbours, and a
 * volume module that imported all three would close an import cycle.
 *
 * Each backend must ALSO read effectiveVolume() when it creates its player
 * (the <audio> element, the Spotify SDK device, the YT iframe), because a
 * sink only hears about CHANGES — a player created after the last change
 * would otherwise start at its own default.
 *
 * Volume is linear 0..1 as stored; the sinks pass it through unshaped.
 */

const STORAGE_KEY = 'vs.volume';
const DEFAULT = { volume: 0.8, muted: false };

let state = null; // loaded lazily, so a test's fresh localStorage is honoured
let snapshot = null; // stable object for useSyncExternalStore
const listeners = new Set();
const sinks = new Set();

const clamp01 = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : null;
};

function load() {
  if (state) return state;
  state = { ...DEFAULT };
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (raw && typeof raw === 'object') {
      const v = clamp01(raw.volume);
      if (v != null) state.volume = v;
      state.muted = !!raw.muted;
    }
  } catch { /* corrupt or blocked storage — defaults */ }
  snapshot = { ...state };
  return state;
}

function commit() {
  snapshot = { ...state };
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot)); } catch { /* blocked */ }
  const eff = effectiveVolume();
  sinks.forEach((fn) => { try { fn(eff, snapshot); } catch { /* a backend mid-teardown */ } });
  listeners.forEach((fn) => fn(snapshot));
}

/** {volume, muted} — the same object until the next change. */
export function getSnapshot() { load(); return snapshot; }

export function getVolume() { return load().volume; }
export function isMuted() { return load().muted; }

/** What the speakers should get: 0 while muted. */
export function effectiveVolume() {
  const s = load();
  return s.muted ? 0 : s.volume;
}

/**
 * Sets the level. Raising it above zero also unmutes — dragging a muted
 * slider up and hearing nothing is the classic bug.
 */
export function setVolume(v) {
  const s = load();
  const next = clamp01(v);
  if (next == null) return;
  const muted = next > 0 ? false : s.muted;
  if (next === s.volume && muted === s.muted) return;
  s.volume = next;
  s.muted = muted;
  commit();
}

export function setMuted(b) {
  const s = load();
  if (s.muted === !!b) return;
  s.muted = !!b;
  commit();
}

export function toggleMute() { setMuted(!load().muted); }

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Backends: called with (effectiveVolume, {volume, muted}) on every change. */
export function registerSink(fn) {
  sinks.add(fn);
  return () => sinks.delete(fn);
}

/** Tests only: forget the loaded state so the next read hits storage again. */
export function __resetForTests() {
  state = null;
  snapshot = null;
  listeners.clear();
  sinks.clear();
}
