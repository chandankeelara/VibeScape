// frontend-next/src/features/queue/dj.js — the DJ event log.
import * as dj from '../../frontend-next/src/features/queue/dj.js';
import * as api from '../../frontend-next/src/lib/api.js';

vi.mock('../../frontend-next/src/lib/api.js', () => ({
  similarTracksDj: vi.fn(async () => ({ tracks: [{ id: 11 }] })),
  similarTracks: vi.fn(async () => ({ tracks: [] })),
}));

const ev = (id, action, ratio = null, ts = 1000) => ({ track_id: id, action, played_ratio: ratio, ts });

describe('appendEvent', () => {
  it('returns the same array for unloggable events', () => {
    const log = [];
    expect(dj.appendEvent(log, ev(1, 'teleported'))).toBe(log);
    expect(dj.appendEvent(log, { action: 'completed' })).toBe(log);
    expect(dj.appendEvent(log, ev('', 'completed'))).toBe(log);
    expect(dj.appendEvent(log, null)).toBe(log);
  });

  it('logs facts only, clamping the ratio', () => {
    const [e] = dj.appendEvent([], ev(5, 'completed', 1.7, 42));
    expect(e).toEqual({ id: 5, action: 'completed', played_ratio: 1, ts: 42 });
  });

  it('keeps repeats — the backend replay bounds them', () => {
    let log = dj.appendEvent([], ev(7, 'searched'));
    log = dj.appendEvent(log, ev(7, 'completed', 1));
    expect(log.map((e) => e.action)).toEqual(['searched', 'completed']);
  });

  it('caps at DJ_MAX_EVENTS, dropping the oldest', () => {
    let log = [];
    for (let i = 0; i < dj.DJ_MAX_EVENTS + 10; i++) log = dj.appendEvent(log, ev(i, 'skipped', 0.1, i));
    expect(log).toHaveLength(dj.DJ_MAX_EVENTS);
    expect(log[0].id).toBe(10);
  });

  it('accepts every action the backend replays', () => {
    for (const a of dj.DJ_ACTIONS) expect(dj.appendEvent([], ev(1, a))).toHaveLength(1);
  });
});

describe('eventsSignature', () => {
  it('moves when an event lands even once the log is full', () => {
    let log = [];
    for (let i = 0; i < dj.DJ_MAX_EVENTS; i++) log = dj.appendEvent(log, ev(i, 'completed', 1, i));
    const next = dj.appendEvent(log, ev(99, 'picked', null, 9999));
    expect(next).toHaveLength(log.length);
    expect(dj.eventsSignature(next)).not.toBe(dj.eventsSignature(log));
  });
});

describe('recentIds', () => {
  it('returns distinct ids within the window only', () => {
    const now = 10 * 3_600_000;
    const log = [
      { id: 1, ts: now - 5 * 3_600_000 }, { id: 2, ts: now - 2 * 3_600_000 },
      { id: 2, ts: now - 10 }, { id: 3, ts: now - 1 },
    ];
    expect(dj.recentIds(log, 3, now)).toEqual([2, 3]);
  });
});

describe('storage', () => {
  it('round-trips the log', () => {
    const log = dj.appendEvent([], ev(1, 'completed', 1, 5));
    dj.persistEvents(log);
    expect(dj.loadEvents()).toEqual(log);
  });

  it('migrates a v3 verdict map so existing tuning survives', () => {
    localStorage.setItem(dj.DJ_STORAGE_KEY, JSON.stringify({
      stamp: '3:http://localhost:5173',
      tracks: {
        941: { id: 941, action: 'completed', verdict: 'positive', weight: 1.75, ts: 300 },
        12: { id: 12, action: 'skipped', verdict: 'negative', weight: 0.88, ts: 100 },
        77: { id: 77, action: 'next', verdict: 'positive', weight: 0.6, ts: 200 },
        5: { id: 5, action: 'searched', verdict: 'positive', weight: 0.8, ts: 400 },
      },
    }));
    const log = dj.loadEvents();
    expect(log.map((e) => e.id)).toEqual([12, 77, 941, 5]);
    expect(dj.classifyTransition(log[0].played_ratio)).toBe('skipped');
    expect(dj.classifyTransition(log[1].played_ratio)).toBe('next');
    expect(dj.classifyTransition(log[2].played_ratio)).toBe('completed');
    expect(log[3].played_ratio).toBeNull();
  });

  it('drops older schemas and other origins', () => {
    localStorage.setItem(dj.DJ_STORAGE_KEY, JSON.stringify({ stamp: '2:http://localhost:5173', events: [{}] }));
    expect(dj.loadEvents()).toEqual([]);
    localStorage.setItem(dj.DJ_STORAGE_KEY, JSON.stringify({ stamp: '4:https://elsewhere', events: [{ id: 1 }] }));
    expect(dj.loadEvents()).toEqual([]);
    localStorage.setItem(dj.DJ_STORAGE_KEY, 'not json');
    expect(dj.loadEvents()).toEqual([]);
  });
});

describe('fetchDjPicks', () => {
  it('sends the newest events as raw facts, never weights', async () => {
    let log = [];
    for (let i = 0; i < 45; i++) log = dj.appendEvent(log, ev(i, 'completed', 1, i));
    const picks = await dj.fetchDjPicks({ id: 42 }, { events: log, seen: [1, 2], queue: [{ id: 3 }], limit: 8 });
    expect(picks).toEqual([{ id: 11 }]);
    const [key, body] = api.similarTracksDj.mock.calls.at(-1);
    expect(key).toBe(42);
    expect(body.mode).toBe('dj');
    expect(body.events).toHaveLength(dj.DJ_MAX_SENT_EVENTS);
    expect(body.events.at(-1).id).toBe(44);
    expect(body).not.toHaveProperty('positive_ids');
    expect(body.exclude_ids).toEqual([3, 1, 2]);   // queue first, so it survives the cap
  });
});

describe('classifyTransition', () => {
  it.each([[0, 'skipped'], [0.44, 'skipped'], [0.45, 'next'], [0.84, 'next'], [0.85, 'completed'], [NaN, 'skipped']])(
    'ratio %s -> %s', (r, want) => expect(dj.classifyTransition(r)).toBe(want));
  it('a natural end is always completed', () => expect(dj.classifyTransition(0.1, { natural: true })).toBe('completed'));
});
