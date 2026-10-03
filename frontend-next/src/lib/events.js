/**
 * The listening-event send queue.
 *
 * A plain module with no React lifecycle — lib/mascotBus.js is the precedent.
 * Nothing here is allowed to delay, throw into, or visibly fail a user action:
 *
 *   - enqueue() is synchronous, allocation-only, and swallows everything.
 *   - No caller ever awaits a send. flush() returns nothing.
 *   - A failed batch is DROPPED, never retried. A retry queue that grows while
 *     the endpoint is down is the exact failure mode this must not have.
 *   - After MAX_CONSECUTIVE_FAILURES the module goes permanently quiet for the
 *     rest of the page's life, so a 500-ing endpoint costs one request per
 *     flush window and then nothing at all.
 *   - No toasts. One console.warn on the trip to dead, never per failure.
 *
 * Unload is the interesting case: the most valuable event (the user leaving
 * mid-track) is still buffered at exactly the moment a normal fetch gets
 * cancelled. See api.postEvents() for why that flush is a keepalive fetch
 * rather than navigator.sendBeacon.
 */

import { postEvents } from './api';
import { getToken } from './session';

/** Buffer cap. Overflow drops the OLDEST — a stale event is worth less than
 *  a fresh one, and this is what bounds a long session on a dead network. */
const MAX_BUFFER = 200;

/** Server cap (backend _EVENT_BATCH_CAP). Anything past it is counted as
 *  `rejected` rather than failing the request, so never exceed it. */
const BATCH_MAX = 50;

const FLUSH_MS = 5000;
const MAX_CONSECUTIVE_FAILURES = 3;

/** Requests one unload flush may issue. Bounded so a huge buffer can't fire
 *  an unbounded burst at pagehide. */
const UNLOAD_BATCHES = 3;

let buffer = [];
let timer = null;
let sending = false;
let failures = 0;
let dead = false;
let installed = false;

/**
 * The last session token we saw at enqueue time.
 *
 * Sign-out order is: AuthContext clears the token, THEN the player tree
 * unmounts and the media layer closes the open play. Without this the final
 * play_end of a session could never be sent. A dead token here just 401s,
 * which trips the breaker and is otherwise inert — api.postEvents deliberately
 * does NOT clear the session on 401 the way api.request() does.
 */
let rememberedToken = '';

function install() {
  if (installed || typeof document === 'undefined') return;
  installed = true;

  // Mobile browsers frequently never fire pagehide/unload for a backgrounded
  // tab, so this is the flush that actually runs most of the time.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush({ unload: true });
  });

  // listenLog.js registers its OWN pagehide listener, which closes the open
  // play and flushes. It always wins the race: it installs during startPlay()
  // and only then calls enqueue(), which is what first reaches install() here
  // — so the final play_end is already buffered by the time this runs, and
  // this handler normally finds an empty buffer. It stays as the backstop for
  // events that were never part of a play.
  window.addEventListener('pagehide', () => flush({ unload: true }));
}

function schedule() {
  if (timer !== null || dead || !buffer.length) return;
  timer = setTimeout(() => {
    timer = null;
    flush();
  }, FLUSH_MS);
}

/**
 * Buffer one event. Never throws, never blocks, never awaits.
 * `ev` is sent to the wire as-is; shaping it is listenLog.js's job.
 */
export function enqueue(ev) {
  if (dead || !ev) return;
  try {
    install();
    const t = getToken();
    if (t) rememberedToken = t;
    // No session means no user to attribute to. Behave as if the feature
    // does not exist rather than buffering events that can never be sent.
    if (!rememberedToken) return;

    buffer.push(ev);
    if (buffer.length > MAX_BUFFER) buffer.splice(0, buffer.length - MAX_BUFFER);
    schedule();
  } catch {
    /* telemetry is never allowed to surface */
  }
}

function send(batch, unload) {
  // Releases the in-flight lock. EVERY exit path must run it or a timed flush
  // would never be allowed again and the buffer would silently stop draining.
  const release = () => { if (!unload) { sending = false; schedule(); } };

  const token = getToken() || rememberedToken;
  if (!token) { release(); return; }

  let p;
  try {
    p = postEvents(batch, { unload, token });
  } catch {
    // fetch() itself threw (no network stack, torn-down worker).
    release();
    return;
  }
  if (!p || typeof p.then !== 'function') {
    release();
    return;
  }

  const settle = (ok) => {
    // 401 is an auth failure, not a data failure, and the backend answers 202
    // for every data problem — so anything non-OK means "this endpoint is not
    // usable", which is exactly what the breaker is for.
    if (ok) failures = 0;
    else noteFailure();
    release();
  };
  p.then((res) => settle(!!(res && res.ok)), () => settle(false));
}

function noteFailure() {
  failures += 1;
  if (failures < MAX_CONSECUTIVE_FAILURES || dead) return;
  dead = true;
  buffer = [];
  if (timer !== null) { clearTimeout(timer); timer = null; }
  // Exactly one line, once per page life — not a storm.
  console.warn('[VibeScape] listening telemetry disabled after repeated send failures');
}

/**
 * Send what is buffered. Fire-and-forget by design: it returns nothing, so no
 * caller can accidentally await telemetry on an interaction path.
 */
export function flush({ unload = false } = {}) {
  if (dead || !buffer.length) return;
  // A timed flush skips while one is in flight, so a slow endpoint cannot fan
  // out. An unload flush ignores that — it is the buffer's last chance.
  if (sending && !unload) return;

  try {
    if (!unload) {
      sending = true;
      send(buffer.splice(0, BATCH_MAX), false);
      return;
    }
    for (let i = 0; i < UNLOAD_BATCHES && buffer.length; i++) {
      send(buffer.splice(0, BATCH_MAX), true);
    }
  } catch {
    // Never surface — but never strand the lock either.
    sending = false;
  }
}

/** Test/diagnostic seam. Not used by the app. */
export function _pending() { return buffer.length; }
