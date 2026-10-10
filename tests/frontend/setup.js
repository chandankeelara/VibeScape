/**
 * Minimal browser surface for modules that touch localStorage, window and
 * document — rebuilt before every test so no state leaks between them.
 * Tests fire captured listeners with `fire('window' | 'document', type)`.
 */

class MemoryStorage {
  constructor() { this.map = new Map(); }
  get length() { return this.map.size; }
  key(i) { return [...this.map.keys()][i] ?? null; }
  getItem(k) { return this.map.has(k) ? this.map.get(k) : null; }
  setItem(k, v) { this.map.set(k, String(v)); }
  removeItem(k) { this.map.delete(k); }
  clear() { this.map.clear(); }
}

function target() {
  const listeners = {};
  return {
    listeners,
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    removeEventListener(type, fn) { listeners[type] = (listeners[type] || []).filter((f) => f !== fn); },
  };
}

beforeEach(() => {
  globalThis.localStorage = new MemoryStorage();
  globalThis.sessionStorage = new MemoryStorage();
  globalThis.window = Object.assign(target(), {
    location: { origin: 'http://localhost:5173' },
    matchMedia: () => ({ matches: false }),
  });
  globalThis.document = Object.assign(target(), { visibilityState: 'visible' });
  globalThis.fire = (where, type, arg) => {
    const t = where === 'window' ? globalThis.window : globalThis.document;
    (t.listeners[type] || []).forEach((fn) => fn(arg));
  };
});
