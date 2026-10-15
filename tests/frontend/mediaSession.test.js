// @vitest-environment jsdom
/**
 * frontend-next/src/media/player.js — the OS media session.
 *
 * 1. Lock screens (iOS, Android) have room for ONE pair of side buttons. With
 *    seekbackward/seekforward registered alongside nexttrack/previoustrack the
 *    seek pair won, so "next" on the lock screen was +10 s and never reached
 *    hooks.onNext — no DJ pick, no media_key skip in telemetry.
 * 2. The silent session anchor that holds the session during Spotify / video
 *    was 0.25 s on loop. Its loops must hand the OS the SONG's position again,
 *    and it must be long enough not to be a sub-5 s "transient" clip.
 *
 * jsdom has no Media Session, so a recording fake stands in for it. None of
 * this proves what a real phone does; it pins down what we ask the phone for.
 */
const MODULE = '../../frontend-next/src/media/player.js';

vi.mock('../../frontend-next/src/lib/events', () => ({ enqueue() {}, flush() {} }));
vi.mock('../../frontend-next/src/media/glow', () => ({ start() {}, stop() {}, setAlpha() {} }));
vi.mock('../../frontend-next/src/media/youtube', () => ({
  setHandlers() {}, init() {}, stop() {}, play() {}, pause() {}, seekTo() {},
  cueOrPlay() {}, getCurrentTime: () => 0, getDuration: () => 0,
}));
const sp = vi.hoisted(() => ({ position: 0, duration: 0 }));
vi.mock('../../frontend-next/src/media/spotify', () => ({
  setOnState() {}, setOnReady() {}, setOnEnded() {}, setOnError() {}, init() {},
  isActive: () => true, playTrack: () => Promise.resolve(true),
  pause() {}, resume() {}, seek() {},
  getDuration: () => sp.duration, getPosition: () => sp.position,
}));
vi.mock('../../frontend-next/src/media/verify', () => ({ init() {}, subscribe: () => () => {}, stop() {} }));

let player;
let handlers;
let positions;
let blobs;

beforeEach(async () => {
  vi.resetModules();
  document.body.innerHTML = '';
  const proto = window.HTMLMediaElement.prototype;
  proto.play = function play() { this.dispatchEvent(new Event('play')); return Promise.resolve(); };
  proto.pause = function pause() {};
  proto.load = function load() {};

  handlers = {};
  positions = [];
  Object.defineProperty(navigator, 'mediaSession', {
    configurable: true,
    value: {
      metadata: null,
      playbackState: 'none',
      setActionHandler(action, fn) { if (fn) handlers[action] = fn; else delete handlers[action]; },
      setPositionState(p) { positions.push(p); },
    },
  });
  window.MediaMetadata = class { constructor(m) { Object.assign(this, m); } };

  blobs = [];
  URL.createObjectURL = (b) => { blobs.push(b); return 'blob:anchor'; };

  sp.position = 0;
  sp.duration = 0;
  player = await import(MODULE);
  player.init();
});

it('registers next/previous track and NOT the +/-10 s seek pair', () => {
  expect(handlers.nexttrack).toBeTypeOf('function');
  expect(handlers.previoustrack).toBeTypeOf('function');
  expect(handlers.seekbackward).toBeUndefined();
  expect(handlers.seekforward).toBeUndefined();
  // The scrubber drag is a different action and stays.
  expect(handlers.seekto).toBeTypeOf('function');
});

it('a lock-screen next reaches onNext as a media_key', () => {
  const onNext = vi.fn();
  const onPrevious = vi.fn();
  player.setHooks({ onNext, onPrevious });
  handlers.nexttrack();
  handlers.previoustrack();
  expect(onNext).toHaveBeenCalledWith({ trigger: 'media_key' });
  expect(onPrevious).toHaveBeenCalledWith({ trigger: 'media_key' });
});

it('setting the PiP handler does not bring the seek pair back', () => {
  player.setPipHandler(() => {});
  expect(handlers.seekbackward).toBeUndefined();
  expect(handlers.seekforward).toBeUndefined();
});

it('the anchor is a silent WAV longer than 5 s, and small', () => {
  player.loadTrack({ id: 1, spotify_id: 'sp1', title: 'T' });
  expect(blobs).toHaveLength(1);
  const seconds = (blobs[0].size - 44) / 8000; // 8 kHz mono 8-bit
  expect(seconds).toBeGreaterThan(5);
  expect(blobs[0].size).toBeLessThan(200_000);
});

it("each anchor loop re-pushes the song's position, not the anchor's", () => {
  player.loadTrack({ id: 1, spotify_id: 'sp1', title: 'T' });
  const anchor = document.getElementById('sessionAnchor');
  expect(anchor).not.toBeNull();

  sp.position = 73.5;
  sp.duration = 211;
  positions.length = 0;
  anchor.dispatchEvent(new Event('seeked')); // what a loop wrap fires
  expect(positions).toEqual([{ position: 73.5, duration: 211, playbackRate: 1 }]);

  // Immediately again — the throttle in emitTime must not swallow it.
  sp.position = 83.5;
  anchor.dispatchEvent(new Event('seeked'));
  expect(positions.at(-1)).toEqual({ position: 83.5, duration: 211, playbackRate: 1 });
});
