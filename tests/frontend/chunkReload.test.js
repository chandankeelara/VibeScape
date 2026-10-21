// A tab that outlived a deploy asks for a chunk the new build deleted; it
// reloads once, a second failure inside the window does not reload (a build
// missing its own chunk cannot loop), and a later deploy reloads again.
import {
  RELOAD_KEY,
  RELOAD_WINDOW_MS,
  _resetReloading,
  importWithReload,
  installPreloadErrorReload,
  isChunkLoadError,
  reloadOnce,
} from '../../frontend-next/src/lib/chunkReload.js';

const CHROME = new TypeError(
  'Failed to fetch dynamically imported module: https://x/assets/LyricsPanel-B-LlEb88.js');

function memoryStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

beforeEach(() => {
  vi.stubGlobal('sessionStorage', memoryStorage());
  _resetReloading();
});
afterEach(() => vi.unstubAllGlobals());

describe('reloadOnce', () => {
  it('reloads on the first failure, not on a second immediate one, and again after the window', () => {
    const reload = vi.fn();
    const t0 = 1_000_000;

    expect(reloadOnce(CHROME, { now: t0, reload })).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(RELOAD_KEY)).toBe(String(t0));

    // The reloaded page fails again straight away: refused.
    _resetReloading();
    expect(reloadOnce(CHROME, { now: t0 + 2_000, reload })).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);

    // Just inside the window, still refused.
    expect(reloadOnce(CHROME, { now: t0 + RELOAD_WINDOW_MS - 1, reload })).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);

    // A later deploy: reloads again.
    expect(reloadOnce(CHROME, { now: t0 + RELOAD_WINDOW_MS, reload })).toBe(true);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it('several failing imports on one page reload it once', () => {
    const reload = vi.fn();
    expect(reloadOnce(CHROME, { now: 5, reload })).toBe(true);
    expect(reloadOnce(CHROME, { now: 6, reload })).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('ignores errors that are not a missing chunk', () => {
    const reload = vi.fn();
    expect(reloadOnce(new TypeError('x is not a function'), { now: 5, reload })).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it('does not reload when sessionStorage is unavailable (no guard, no loop)', () => {
    vi.stubGlobal('sessionStorage', {
      getItem: () => { throw new Error('SecurityError'); },
      setItem: () => { throw new Error('SecurityError'); },
    });
    const reload = vi.fn();
    expect(reloadOnce(CHROME, { now: 5, reload })).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});

describe('isChunkLoadError', () => {
  it.each([
    'Failed to fetch dynamically imported module: /assets/a.js',
    'error loading dynamically imported module: /assets/a.js',
    'Importing a module script failed.',
    'Unable to preload CSS for /assets/a.css',
  ])('%s', (message) => expect(isChunkLoadError(new Error(message))).toBe(true));

  it('ChunkLoadError by name', () => {
    const e = new Error('Loading chunk 3 failed');
    e.name = 'ChunkLoadError';
    expect(isChunkLoadError(e)).toBe(true);
  });
});

describe('importWithReload', () => {
  it('holds the import while the page reloads', async () => {
    const reload = vi.fn();
    vi.stubGlobal('window', { location: { reload } });
    let settled = false;
    importWithReload(() => Promise.reject(CHROME)).then(
      () => { settled = true; }, () => { settled = true; });
    await new Promise((r) => setTimeout(r, 10));
    expect(reload).toHaveBeenCalledTimes(1);
    expect(settled).toBe(false);
  });

  it('falls back instead of crashing the app when the guard refuses', async () => {
    sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
    const reload = vi.fn();
    vi.stubGlobal('window', { location: { reload } });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const fallback = { default: () => null };
    await expect(importWithReload(() => Promise.reject(CHROME), fallback)).resolves.toBe(fallback);
    expect(reload).not.toHaveBeenCalled();
  });

  it('passes a module through untouched', async () => {
    const mod = { default: 1 };
    await expect(importWithReload(() => Promise.resolve(mod))).resolves.toBe(mod);
  });
});

describe("Vite's vite:preloadError", () => {
  it('reloads once', () => {
    const reload = vi.fn();
    vi.stubGlobal('window', { location: { reload } });
    const target = new EventTarget();
    installPreloadErrorReload(target);
    const e = new Event('vite:preloadError', { cancelable: true });
    e.payload = CHROME;
    target.dispatchEvent(e);
    expect(reload).toHaveBeenCalledTimes(1);
    // Never cancelled: Vite would then resolve the import to undefined.
    expect(e.defaultPrevented).toBe(false);
  });
});
