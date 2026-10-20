// @vitest-environment jsdom
/**
 * frontend-next/src/features/player/bit/useBitState.js — the three things the
 * S2 dock depends on that the old fixed dock never exercised:
 *   - the groove move is chosen per mood BAND, so dragging the slider inside
 *     one band does not re-roll his dance on every step;
 *   - mounting mid-song is not a track starting (no cueing on mount), which
 *     matters because "Show Bit" mounts him mid-song;
 *   - a reaction in flight (a skip's sulk) is not cut off by the replacement
 *     track's cueing.
 */
import { act, render, cleanup } from '@testing-library/react';
import { GROOVE_BY_BAND } from '../../frontend-next/src/features/player/bit/manifest';

let bit;
let useBitState;

beforeAll(async () => {
  ({ default: useBitState } = await import('../../frontend-next/src/features/player/bit/useBitState'));
});
afterEach(cleanup);

function Probe(props) {
  bit = useBitState(props);
  return null;
}

const T1 = { id: 1, tempo: 120 };
const T2 = { id: 2, tempo: 120 };
const base = { loadingTrack: false, verifying: false, mode: 'audio', source: 'spotify' };

describe('useBitState', () => {
  it('keeps the same groove move while the vibe moves inside one band', () => {
    const { rerender } = render(<Probe {...base} current={T1} playing vibe={41} />);
    expect(bit.state).toBe('groove');
    const first = bit.animKey;
    expect(GROOVE_BY_BAND.steady).toContain(first);
    for (const v of [44, 47, 52, 55, 59]) {
      rerender(<Probe {...base} current={T1} playing vibe={v} />);
      expect(bit.animKey).toBe(first);
    }
    // Crossing into the next band does re-pick, from that band.
    rerender(<Probe {...base} current={T1} playing vibe={85} />);
    expect(GROOVE_BY_BAND.beast).toContain(bit.animKey);
  });

  it('does not cue a record just because he mounted mid-song', () => {
    render(<Probe {...base} current={T1} playing vibe={50} />);
    expect(bit.state).toBe('groove');
  });

  it('cues when a new track starts', () => {
    const { rerender } = render(<Probe {...base} current={T1} playing vibe={50} />);
    rerender(<Probe {...base} current={T2} playing vibe={50} />);
    expect(bit.state).toBe('cueing');
  });

  it('lets a skip reaction finish instead of cueing over it', () => {
    const { rerender } = render(<Probe {...base} current={T1} playing vibe={50} />);
    act(() => bit.fire('sulk'));
    expect(bit.state).toBe('sulk');
    rerender(<Probe {...base} current={T2} playing vibe={50} />);
    expect(bit.state).toBe('sulk');
  });
});
