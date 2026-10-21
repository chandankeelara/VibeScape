/**
 * Recover a tab that outlived a deploy.
 *
 * Every lazily loaded part of the app (lyrics, Bit's dock, the sync / metrics
 * panels) is a hashed chunk, and a rebuild deletes the old names. A tab still
 * running the previous build asks for a chunk that no longer exists, the
 * server answers 404, and the import rejects with "Failed to fetch dynamically
 * imported module". The only fix is the new index.html, so reload — once.
 *
 * The guard is a timestamp in sessionStorage, not a flag: a reload within
 * RELOAD_WINDOW_MS of the last one is refused, so a build that is itself
 * missing a chunk cannot loop, while a later, genuine deploy still reloads.
 * No sessionStorage (blocked storage) means no guard, so no reload.
 *
 * A reload restarts playback; the DJ session resumes from its log on load.
 */
import { lazy } from 'react';

export const RELOAD_KEY = 'vibescape:chunk-reload-at';
export const RELOAD_WINDOW_MS = 60_000;

// Chrome / Edge, Firefox, Safari, Vite's CSS preload, webpack-style name.
const CHUNK_ERROR =
  /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Unable to preload CSS/i;

export function isChunkLoadError(err) {
  if (!err) return false;
  if (err.name === 'ChunkLoadError') return true;
  return CHUNK_ERROR.test(String(err.message ?? err));
}

// Set once a reload is underway on this page, so every other failing import
// in the same tick waits for it instead of each consulting the guard.
let reloading = false;

/**
 * Reload for a chunk-load error unless one ran within the window. Returns
 * true when the page is (already) reloading.
 */
export function reloadOnce(err, { now = Date.now(), reload = () => window.location.reload() } = {}) {
  if (reloading) return true;
  if (!isChunkLoadError(err)) return false;
  try {
    const last = Number(sessionStorage.getItem(RELOAD_KEY));
    if (last && now - last < RELOAD_WINDOW_MS) return false;
    sessionStorage.setItem(RELOAD_KEY, String(now));
  } catch {
    return false;
  }
  reloading = true;
  reload();
  return true;
}

/** Test seam: forget an in-flight reload. */
export function _resetReloading() {
  reloading = false;
}

const Nothing = () => null;

/**
 * Run a dynamic import, reloading on a chunk-load failure. While the reload
 * is underway the promise never settles (the Suspense fallback holds). If the
 * guard refuses, `fallback` stands in for the module: there is no error
 * boundary above these imports, so a rejection would unmount the whole app
 * over a lyrics panel.
 */
export function importWithReload(load, fallback) {
  return load().catch((err) => {
    if (reloadOnce(err)) return new Promise(() => {});
    if (fallback === undefined) throw err;
    console.error('[chunk] import failed, not reloading again so soon:', err);
    return fallback;
  });
}

/** `React.lazy` with the reload; a refused reload renders nothing. */
export function lazyWithReload(load) {
  return lazy(() => importWithReload(load, { default: Nothing }));
}

/**
 * Vite's own hook: its preload helper dispatches `vite:preloadError` for a
 * failed chunk or CSS preload before rethrowing. Covers any dynamic import not
 * routed through the wrappers above. Never preventDefault: the helper would
 * then resolve the import to undefined, which `lazy` cannot render.
 */
export function installPreloadErrorReload(target = window) {
  target.addEventListener('vite:preloadError', (e) => {
    reloadOnce(e.payload);
  });
}
