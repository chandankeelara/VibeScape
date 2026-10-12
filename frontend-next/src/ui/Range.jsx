import styles from './Range.module.css';

/** Thumb diameter per variant, in px — must match Range.module.css. */
const THUMB = { seekBar: 12, seekPhone: 12, hairline: 0, vibe: 22, volume: 12, default: 12 };

/**
 * A styled native range input. `variant` picks the look; everything else is
 * passed straight to the <input>, so keyboard handling, aria-* and the
 * pointer/key commit handlers stay the caller's.
 *
 * The fill stop tracks the thumb CENTRE, which native ranges inset by half a
 * thumb — so at 0 and 100 the fill meets the thumb exactly.
 */
export default function Range({ variant = 'default', value, min = 0, max = 100, className = '', style, ...rest }) {
  const span = Number(max) - Number(min);
  const f = span > 0 ? Math.min(1, Math.max(0, (Number(value) - Number(min)) / span)) : 0;
  const th = THUMB[variant] ?? THUMB.default;
  const p = `calc(${th / 2}px + (100% - ${th}px) * ${f})`;
  return (
    <input
      type="range"
      className={`${styles.range} ${styles[variant] || ''} ${className}`}
      value={value}
      min={min}
      max={max}
      style={{ ...style, '--p': p }}
      {...rest}
    />
  );
}
