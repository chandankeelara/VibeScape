// frontend-next/src/lib/listenLog.js — what reaches POST /api/events.
const sent = vi.hoisted(() => []);
vi.mock('../../frontend-next/src/lib/events.js', () => ({
  enqueue: (e) => sent.push(e),
  flush: () => {},
}));

const MODULE = '../../frontend-next/src/lib/listenLog.js';
const T0 = 1_800_000_000_000;
const tick = (ms) => vi.setSystemTime(Date.now() + ms);

/** A fresh module instance is a fresh page (new page id, new session). */
async function page({ position = 108000, duration = 211000 } = {}) {
  vi.resetModules();
  const log = await import(MODULE);
  log.setClock(() => ({ position_ms: position, duration_ms: duration }));
  log.setContextProvider(() => ({ vibe: 60, vibe_source: 'user', dj_mode: true }));
  return log;
}
const ofType = (t) => sent.filter((e) => e.type === t);
const slots = () => [...localStorage.map.keys()].filter((k) => k.startsWith('vibescape.openPlay.'));

beforeEach(() => {
  sent.length = 0;
  vi.useFakeTimers();
  vi.setSystemTime(T0);
});
afterEach(() => vi.useRealTimers());

describe('a play', () => {
  it('counts only audible time: pauses and seek jumps are excluded', async () => {
    const log = await page();
    log.startPlay({ id: 1728 }, { source: 'dj', playback: 'spotify' });
    log.setPlaying(true); tick(20_000);
    log.notePause(); log.setPlaying(false); tick(60_000);
    log.noteResume(); log.setPlaying(true); tick(10_000);
    log.noteSeek(30_000, 100_000); tick(5_000);
    log.endPlay('skipped', { trigger: 'next_button' });

    expect(sent.map((e) => e.type)).toEqual(['session_start', 'play_start', 'pause', 'resume', 'seek', 'play_end']);
    const [end] = ofType('play_end');
    expect(end).toMatchObject({
      track_id: 1728, reason: 'skipped', trigger: 'next_button', listened_ms: 35_000,
      position_ms: 108000, duration_ms: 211000, playback: 'spotify', source: 'dj',
      vibe: 60, vibe_source: 'user', dj_mode: true,
    });
    expect(ofType('seek')[0].data).toEqual({ from_ms: 30_000, to_ms: 100_000 });
  });

  it('puts the envelope on every event', async () => {
    const log = await page();
    log.startPlay({ id: 1 }, { source: 'pick', playback: 'preview' });
    log.endPlay('skipped');
    const sid = sent[0].session_id;
    for (const e of sent) {
      expect(e.session_id).toBe(sid);
      expect(Number.isInteger(e.tz_offset_min)).toBe(true);
      expect(e.client_ts).toBeGreaterThan(0);
    }
  });

  it('a completed play carries no trigger', async () => {
    const log = await page();
    log.startPlay({ id: 1 }, { playback: 'preview' }); log.setPlaying(true); tick(30_000);
    log.endPlay('completed', { trigger: 'next_button' });
    expect(ofType('play_end')[0]).not.toHaveProperty('trigger');
    expect(ofType('play_end')[0].listened_ms).toBe(30_000);
  });

  it('reports what actually played after a fallback', async () => {
    const log = await page();
    log.startPlay({ id: 1 }, { playback: 'spotify' }); log.setPlayback('preview');
    log.endPlay('skipped');
    expect(ofType('play_start')[0].playback).toBe('spotify');
    expect(ofType('play_end')[0].playback).toBe('preview');
  });

  it('drops source and playback values outside the contract', async () => {
    const log = await page();
    log.startPlay({ id: 1 }, { source: 'telepathy', playback: 'vinyl' });
    expect(ofType('play_start')[0]).not.toHaveProperty('source');
    expect(ofType('play_start')[0]).not.toHaveProperty('playback');
  });

  it('closes exactly once', async () => {
    const log = await page();
    log.startPlay({ id: 1 }); log.endPlay('skipped'); log.endPlay('skipped');
    expect(ofType('play_end')).toHaveLength(1);
  });

  it('ignores pause / resume / seek with nothing open', async () => {
    const log = await page();
    log.notePause(); log.noteResume(); log.noteSeek(1, 2);
    expect(sent).toEqual([]);
  });

  it('identifies a track by spotify id first', async () => {
    const log = await page();
    log.startPlay({ id: 9, spotify_id: 'abcdefghijklmnopqrstuv' });
    expect(ofType('play_start')[0]).toMatchObject({ spotify_id: 'abcdefghijklmnopqrstuv' });
    expect(ofType('play_start')[0]).not.toHaveProperty('track_id');
  });
});

describe('a page that goes away', () => {
  it('pagehide closes the play as abandoned and ends the session', async () => {
    const log = await page();
    log.startPlay({ id: 1 }); log.setPlaying(true); tick(5000);
    fire('window', 'pagehide');
    expect(sent.at(-2)).toMatchObject({ type: 'play_end', reason: 'abandoned', listened_ms: 5000 });
    expect(sent.at(-1)).toMatchObject({ type: 'session_end' });
    expect(slots()).toEqual([]);
  });

  it('a killed page is recovered once, by a later page, with what it last saved', async () => {
    const a = await page();
    a.startPlay({ id: 555 }, { source: 'dj', playback: 'spotify' }); a.setPlaying(true);
    vi.advanceTimersByTime(15_000);                      // A's heartbeat saves 15 s heard
    expect(slots()).toHaveLength(1);
    // A dies here: no pagehide. A later page opens after the heartbeat is stale.
    tick(60_000);
    const b = await page();
    b.startPlay({ id: 600 });
    const abandoned = sent.filter((e) => e.reason === 'abandoned');
    expect(abandoned).toHaveLength(1);
    expect(abandoned[0]).toMatchObject({ track_id: 555, listened_ms: 15_000, playback: 'spotify' });
    expect(abandoned[0]).not.toHaveProperty('vibe');     // the slider now says nothing about then
    expect(abandoned[0].session_id).not.toBe(ofType('play_start').at(-1).session_id);
  });

  it('a live second tab is never recovered as abandoned', async () => {
    const a = await page();
    a.startPlay({ id: 555 }); a.setPlaying(true);
    vi.advanceTimersByTime(15_000);
    tick(10_000);                                        // A is still heartbeating
    const b = await page();
    b.startPlay({ id: 600 });
    expect(sent.filter((e) => e.reason === 'abandoned')).toEqual([]);
  });
});

describe('sessions and app events', () => {
  it('a return after 30+ idle minutes is a new session', async () => {
    const log = await page();
    log.logEvent('search', { data: { query: 'x' } });
    const first = sent.at(-1).session_id;
    document.visibilityState = 'hidden'; fire('document', 'visibilitychange');
    tick(31 * 60_000);
    document.visibilityState = 'visible'; fire('document', 'visibilitychange');
    expect(sent.slice(-2).map((e) => e.type)).toEqual(['session_end', 'session_start']);
    expect(sent.at(-1).session_id).not.toBe(first);
  });

  it('a short absence keeps the session', async () => {
    const log = await page();
    log.logEvent('dj_toggle', { data: { on: true } });
    document.visibilityState = 'hidden'; fire('document', 'visibilitychange');
    tick(5 * 60_000);
    document.visibilityState = 'visible'; fire('document', 'visibilitychange');
    expect(ofType('session_end')).toEqual([]);
  });

  it('logTrackEvent carries the track identity', async () => {
    const log = await page();
    log.logTrackEvent('queue_add', { id: 77 }, { data: { via: 'rec' } });
    expect(ofType('queue_add')[0]).toMatchObject({ track_id: 77, data: { via: 'rec' } });
  });
});
