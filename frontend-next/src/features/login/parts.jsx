/**
 * Small shared pieces for the auth card — written once rather than repeated
 * per view as the legacy markup did.
 *
 * No innerHTML anywhere: the legacy `escapeHtml` dance disappears because JSX
 * escapes text nodes by default.
 */

import styles from './Auth.module.css';

const svgProps = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
};

export const ChevronLeftIcon = () => (
  <svg {...svgProps} width="16" height="16">
    <polyline points="15 18 9 12 15 6" />
  </svg>
);

export const MailIcon = () => (
  <svg {...svgProps} width="16" height="16">
    <rect x="2" y="4" width="20" height="16" rx="2" />
    <path d="m2 7 10 6 10-6" />
  </svg>
);

export const PlusIcon = () => (
  <svg {...svgProps} width="14" height="14">
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  </svg>
);

/** Play triangle — the "Just listen" guest affordance from login.html:169. */
export const PlayIcon = () => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
    <path d="M8 5v14l11-7z" />
  </svg>
);

export const SpotifyIcon = () => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true">
    <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm4.586 14.424a.623.623 0 0 1-.857.207c-2.348-1.435-5.304-1.76-8.785-.964a.623.623 0 1 1-.277-1.215c3.809-.871 7.077-.496 9.712 1.115.293.18.386.563.207.857Zm1.223-2.722a.78.78 0 0 1-1.072.257c-2.687-1.652-6.785-2.13-9.965-1.166a.78.78 0 1 1-.452-1.492c3.632-1.102 8.147-.568 11.232 1.329a.78.78 0 0 1 .257 1.072Zm.105-2.835c-3.223-1.914-8.54-2.09-11.617-1.156a.935.935 0 1 1-.542-1.79c3.532-1.072 9.404-.865 13.115 1.338a.935.935 0 1 1-.956 1.608Z" />
  </svg>
);

export function BackButton({ onClick, label = 'Back to sign-in options' }) {
  return (
    <button className={styles.back} type="button" onClick={onClick} aria-label={label}>
      <ChevronLeftIcon />
      <span>Back</span>
    </button>
  );
}

export function Spinner({ label }) {
  return (
    <div className={styles.loading}>
      <div className={styles.spinner} aria-hidden="true" />
      <p className={styles.hint}>{label}</p>
    </div>
  );
}

/**
 * A full-width provider/option row. Ports the `.cta` block from
 * frontend/login.html:150-178 — icon, title, sub-label, arrow.
 */
export function OptionButton({ icon, title, sub, onClick, disabled, variant }) {
  return (
    <button
      type="button"
      className={`${styles.option} ${variant === 'spotify' ? styles.optionSpotify : ''}`}
      onClick={onClick}
      disabled={disabled}
    >
      <span className={styles.optionIcon} aria-hidden="true">
        {icon}
      </span>
      <span className={styles.optionLabel}>
        <span className={styles.optionTitle}>{title}</span>
        <span className={styles.optionSub}>{sub}</span>
      </span>
      <span className={styles.optionArrow} aria-hidden="true">
        →
      </span>
    </button>
  );
}
