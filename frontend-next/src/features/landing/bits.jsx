/**
 * Shared marketing primitives used by more than one landing section.
 * Structure from frontend/login.html, styling from frontend/login.css.
 */

import styles from './bits.module.css';

export function Eyebrow({ children }) {
  return (
    <span className={styles.eyebrow}>
      <span className={styles.dot} aria-hidden="true" />
      <span>{children}</span>
    </span>
  );
}

/** The italic Instrument Serif accent word. Colour follows `--accent`. */
export function SerifEm({ children, className = '' }) {
  return <span className={`${styles.serifEm} ${className}`}>{children}</span>;
}

export function Brand({ className = '', markClassName = '', nameClassName = '' }) {
  return (
    <span className={`${styles.brand} ${className}`}>
      <span className={`${styles.brandMark} ${markClassName}`} aria-hidden="true" />
      <span className={nameClassName}>VibeScape</span>
    </span>
  );
}

export function SectionHead({ eyebrow, children, lede, tight = false }) {
  return (
    <header className={`${styles.sectionHead} ${tight ? styles.tight : ''}`}>
      <Eyebrow>{eyebrow}</Eyebrow>
      <h2>{children}</h2>
      {lede && <p className={styles.sectionLede}>{lede}</p>}
    </header>
  );
}
