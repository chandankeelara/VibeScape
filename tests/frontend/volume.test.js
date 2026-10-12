import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as volume from '../../frontend-next/src/media/volume.js';

beforeEach(() => volume.__resetForTests());

describe('volume', () => {
  it('defaults to 0.8, unmuted', () => {
    expect(volume.getVolume()).toBe(0.8);
    expect(volume.isMuted()).toBe(false);
    expect(volume.effectiveVolume()).toBe(0.8);
  });

  it('clamps to 0..1 and ignores non-numbers', () => {
    volume.setVolume(1.7);
    expect(volume.getVolume()).toBe(1);
    volume.setVolume(-3);
    expect(volume.getVolume()).toBe(0);
    volume.setVolume(0.4);
    volume.setVolume('loud');
    volume.setVolume(NaN);
    expect(volume.getVolume()).toBe(0.4);
  });

  it('effective volume is 0 while muted and comes back on unmute', () => {
    volume.setVolume(0.6);
    volume.toggleMute();
    expect(volume.isMuted()).toBe(true);
    expect(volume.effectiveVolume()).toBe(0);
    expect(volume.getVolume()).toBe(0.6);
    volume.toggleMute();
    expect(volume.effectiveVolume()).toBe(0.6);
  });

  it('raising the level unmutes', () => {
    volume.setMuted(true);
    volume.setVolume(0.3);
    expect(volume.isMuted()).toBe(false);
  });

  it('persists across a reload', () => {
    volume.setVolume(0.25);
    volume.setMuted(true);
    volume.__resetForTests();
    expect(volume.getVolume()).toBe(0.25);
    expect(volume.isMuted()).toBe(true);
  });

  it('survives corrupt or out-of-range storage', () => {
    localStorage.setItem('vs.volume', 'not json');
    expect(volume.getVolume()).toBe(0.8);
    volume.__resetForTests();
    localStorage.setItem('vs.volume', JSON.stringify({ volume: 9, muted: 1 }));
    expect(volume.getVolume()).toBe(1);
    expect(volume.isMuted()).toBe(true);
  });

  it('notifies subscribers and sinks on change only, with a stable snapshot between', () => {
    const sub = vi.fn();
    const sink = vi.fn();
    volume.subscribe(sub);
    volume.registerSink(sink);
    const before = volume.getSnapshot();
    expect(volume.getSnapshot()).toBe(before);

    volume.setVolume(0.5);
    expect(sub).toHaveBeenCalledTimes(1);
    expect(sink).toHaveBeenLastCalledWith(0.5, { volume: 0.5, muted: false });
    expect(volume.getSnapshot()).not.toBe(before);

    volume.setVolume(0.5); // no change
    volume.setMuted(false); // no change
    expect(sub).toHaveBeenCalledTimes(1);

    volume.setMuted(true);
    expect(sink).toHaveBeenLastCalledWith(0, { volume: 0.5, muted: true });
  });

  it('unsubscribe stops notifications, and a throwing sink does not block others', () => {
    const sub = vi.fn();
    const off = volume.subscribe(sub);
    const good = vi.fn();
    volume.registerSink(() => { throw new Error('torn down'); });
    volume.registerSink(good);
    off();
    volume.setVolume(0.1);
    expect(sub).not.toHaveBeenCalled();
    expect(good).toHaveBeenCalledWith(0.1, expect.any(Object));
  });
});
