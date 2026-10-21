// @vitest-environment jsdom
/**
 * frontend-next/src/ui/Marquee.jsx — the one-line title that scrolls only
 * when it has to:
 *   - a line that fits is still (no animation, no fade mask);
 *   - a line that overflows gets one infinite ping-pong animation: rest at
 *     the start, slide to the end at ~34px/s, rest, slide back;
 *   - under reduced motion it never animates (the CSS ends it in an ellipsis);
 *   - hover holds it, and leaving lets it go;
 *   - the full text is in the DOM and the tooltip either way.
 *
 * jsdom has no layout, so widths come from the stubs below: the root is
 * BOX wide and its text TEXT wide.
 */
import { act, cleanup, render } from '@testing-library/react';
import Marquee, { MARQUEE_REST_MS, MARQUEE_SPEED } from '../../frontend-next/src/ui/Marquee';

let BOX = 300;
let TEXT = 200;
let reduce = false;
let animations = [];

const desc = (proto, name) => Object.getOwnPropertyDescriptor(proto, name);
const saved = {};

beforeAll(() => {
  saved.client = desc(HTMLElement.prototype, 'clientWidth') || desc(Element.prototype, 'clientWidth');
  saved.scroll = desc(HTMLElement.prototype, 'scrollWidth') || desc(Element.prototype, 'scrollWidth');
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get() { return this.title ? BOX : 0; } });
  Object.defineProperty(HTMLElement.prototype, 'scrollWidth', { configurable: true, get() { return this.title ? BOX : TEXT; } });
  HTMLElement.prototype.animate = function animate(keyframes, options) {
    const a = { keyframes, options, state: 'running', cancel() { this.state = 'cancelled'; }, pause() { this.state = 'paused'; }, play() { this.state = 'running'; } };
    animations.push(a);
    return a;
  };
  window.matchMedia = (q) => ({ matches: q.includes('reduce') && reduce, media: q, addEventListener() {}, removeEventListener() {} });
});
afterAll(() => {
  if (saved.client) Object.defineProperty(HTMLElement.prototype, 'clientWidth', saved.client);
  if (saved.scroll) Object.defineProperty(HTMLElement.prototype, 'scrollWidth', saved.scroll);
  delete HTMLElement.prototype.animate;
});
beforeEach(() => { BOX = 300; TEXT = 200; reduce = false; animations = []; });
afterEach(cleanup);

const LONG = 'The Long Way Round the Harbour When the Lights Go Out';
const root = (c) => c.querySelector('[title]');

describe('Marquee', () => {
  it('holds still when the text fits', () => {
    const { container } = render(<Marquee text="Ok" />);
    expect(animations).toHaveLength(0);
    expect(root(container).hasAttribute('data-over')).toBe(false);
  });

  it('ping-pongs an overflowing line, resting at both ends', () => {
    TEXT = 500;
    const { container } = render(<Marquee text={LONG} />);
    expect(root(container).hasAttribute('data-over')).toBe(true);
    expect(animations).toHaveLength(1);
    const [{ keyframes, options }] = animations;
    // jsdom computes no padding, so no fade: the travel is exactly the overflow.
    expect(keyframes.map((k) => k.transform)).toEqual([
      'translateX(0)', 'translateX(0)', 'translateX(-200px)', 'translateX(-200px)', 'translateX(0)',
    ]);
    const slide = (200 / MARQUEE_SPEED) * 1000;
    expect(options.iterations).toBe(Infinity);
    expect(options.duration).toBeCloseTo(2 * (slide + MARQUEE_REST_MS));
    expect(keyframes[1].offset).toBeCloseTo(MARQUEE_REST_MS / options.duration);
  });

  it('does not move under reduced motion, and still marks the overflow for the ellipsis', () => {
    TEXT = 500;
    reduce = true;
    const { container } = render(<Marquee text={LONG} />);
    expect(animations).toHaveLength(0);
    expect(root(container).hasAttribute('data-over')).toBe(true);
  });

  it('keeps the whole text for assistive tech and the tooltip', () => {
    TEXT = 500;
    const { container } = render(<Marquee text={LONG} />);
    expect(root(container).title).toBe(LONG);
    expect(container.textContent).toBe(LONG);
  });

  it('pauses while hovered and resumes after', () => {
    TEXT = 500;
    const { container } = render(<Marquee text={LONG} />);
    const el = root(container);
    act(() => { el.dispatchEvent(new Event('pointerenter')); });
    expect(animations[0].state).toBe('paused');
    act(() => { el.dispatchEvent(new Event('pointerleave')); });
    expect(animations[0].state).toBe('running');
  });

  it('pauses while the control it sits in has focus', () => {
    TEXT = 500;
    const { container } = render(<button type="button"><Marquee text={LONG} /></button>);
    const button = container.querySelector('button');
    act(() => { button.focus(); });
    expect(animations[0].state).toBe('paused');
    act(() => { button.blur(); });
    expect(animations[0].state).toBe('running');
  });

  it('starts over for a new text, and stops when the new one fits', () => {
    TEXT = 500;
    const { rerender } = render(<Marquee text={LONG} />);
    TEXT = 100;
    rerender(<Marquee text="Ok" />);
    expect(animations[0].state).toBe('cancelled');
    expect(animations).toHaveLength(1);
  });
});
