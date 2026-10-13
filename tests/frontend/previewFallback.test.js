// @vitest-environment jsdom
/**
 * frontend-next/src/media/player.js — the preview_url -> /api/stream fallback
 * is armed for the CURRENT track only.
 *
 * setPreviewSource added an `error` listener with { once: true } for every
 * track with a preview_url and never removed it when the preview loaded, so
 * the element collected one stale fallback per track. The first real error
 * then ran all of them, each pointing src at its own old track's stream.
 *
 * Real media facade and listening log; YouTube / Spotify / Web Audio / verify
 * and the telemetry transport are faked, and jsdom's unimplemented
 * play / pause / load are stubbed.
 */
const MODULE = '../../frontend-next/src/media/player.js';

vi.mock('../../frontend-next/src/lib/events', () => ({ enqueue() {}, flush() {} }));
vi.mock('../../frontend-next/src/media/glow', () => ({ start() {}, stop() {}, setAlpha() {} }));
vi.mock('../../frontend-next/src/media/youtube', () => ({
  setHandlers() {}, init() {}, stop() {}, play() {}, pause() {}, seekTo() {},
  cueOrPlay() {}, getCurrentTime: () => 0, getDuration: () => 0,
}));
vi.mock('../../frontend-next/src/media/spotify', () => ({
  setOnState() {}, setOnReady() {}, setOnEnded() {}, setOnError() {}, init() {},
  isActive: () => false, pause() {}, resume() {}, seek() {},
  getDuration: () => 0, getPosition: () => 0,
}));
vi.mock('../../frontend-next/src/media/verify', () => ({ init() {}, subscribe: () => () => {}, stop() {} }));

const track = (n, preview = true) => ({
  id: n,
  spotify_id: `sp${n}`,
  preview_url: preview ? `https://cdn.test/${n}.mp3` : null,
});
const stream = (n) => `/api/stream/sp${n}`;

let player;
let el;
let srcSets;
let playCalls;

beforeEach(async () => {
  vi.resetModules();
  document.body.innerHTML = '';
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  const proto = window.HTMLMediaElement.prototype;
  playCalls = 0;
  proto.play = function play() { playCalls += 1; return Promise.resolve(); };
  proto.pause = function pause() {};
  proto.load = function load() {};

  player = await import(MODULE);
  player.init();
  el = document.getElementById('player');

  // Record every src assignment, then let the real setter run.
  const real = Object.getOwnPropertyDescriptor(proto, 'src');
  srcSets = [];
  Object.defineProperty(el, 'src', {
    configurable: true,
    get() { return real.get.call(this); },
    set(v) { srcSets.push(v); real.set.call(this, v); },
  });
});

/** Src assignments made by an error, as paths (jsdom resolves them). */
function srcSetByError() {
  srcSets.length = 0;
  playCalls = 0;
  el.dispatchEvent(new Event('error'));
  return srcSets.map((v) => new URL(v, 'http://x').pathname);
}

it('after three previews load, one error falls back once, to the current track', () => {
  player.loadTrack(track(1));
  player.loadTrack(track(2));
  player.loadTrack(track(3));

  expect(srcSetByError()).toEqual([stream(3)]);
  expect(playCalls).toBe(1);
});

it('the fallback fires exactly once per track', () => {
  player.loadTrack(track(1));
  player.loadTrack(track(2));
  expect(srcSetByError()).toEqual([stream(2)]);
  // The stream itself failing must not loop, and must not revive track 1.
  expect(srcSetByError()).toEqual([]);
});

it('a track with no preview_url inherits no fallback from earlier tracks', () => {
  player.loadTrack(track(1));
  player.loadTrack(track(2));
  player.loadTrack(track(3, false));
  expect(srcSetByError()).toEqual([]);
});

it('stop() disarms the fallback', () => {
  player.loadTrack(track(1));
  player.stop();
  expect(srcSetByError()).toEqual([]);
});
