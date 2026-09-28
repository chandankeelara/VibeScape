/**
 * Inline SVG icons for the landing page. All paths copied from
 * frontend/login.html so the marks stay pixel-identical to the legacy page.
 * Every icon is decorative — the surrounding button carries the label.
 */

const base = { viewBox: '0 0 24 24', 'aria-hidden': 'true', focusable: 'false' };

export function LoginIcon({ className }) {
  return (
    <svg
      {...base}
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" />
      <polyline points="10 17 15 12 10 7" />
      <line x1="15" y1="12" x2="3" y2="12" />
    </svg>
  );
}

export function PlayIcon({ className }) {
  return (
    <svg {...base} className={className}>
      <path fill="currentColor" d="M8 5v14l11-7z" />
    </svg>
  );
}

export function PrevIcon({ className }) {
  return (
    <svg {...base} className={className}>
      <path fill="currentColor" d="M6 6h2v12H6zm3.5 6l8.5 6V6z" />
    </svg>
  );
}

export function NextIcon({ className }) {
  return (
    <svg {...base} className={className}>
      <path fill="currentColor" d="M6 18l8.5-6L6 6v12zM16 6v12h2V6z" />
    </svg>
  );
}

export function CloseIcon({ className }) {
  return (
    <svg
      {...base}
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

export function SpotifyIcon({ className }) {
  return (
    <svg {...base} className={className}>
      <path
        fill="currentColor"
        d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0Zm5.52 17.34a.75.75 0 0 1-1.03.25c-2.82-1.72-6.36-2.11-10.54-1.16a.75.75 0 1 1-.33-1.46c4.57-1.04 8.5-.6 11.66 1.34.35.21.46.68.24 1.03Zm1.47-3.27a.94.94 0 0 1-1.29.31c-3.23-1.98-8.14-2.55-11.96-1.4a.94.94 0 1 1-.54-1.8c4.36-1.31 9.77-.67 13.48 1.6a.94.94 0 0 1 .31 1.29Zm.13-3.4c-3.86-2.3-10.24-2.51-13.93-1.4a1.12 1.12 0 1 1-.65-2.15c4.24-1.28 11.28-1.04 15.72 1.6a1.12 1.12 0 1 1-1.14 1.95Z"
      />
    </svg>
  );
}

export function YouTubeMusicIcon({ className }) {
  return (
    <svg {...base} className={className}>
      <path
        fill="currentColor"
        d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2Zm-2 14.5v-9l7 4.5-7 4.5Z"
      />
    </svg>
  );
}

export function AppleMusicIcon({ className }) {
  return (
    <svg {...base} className={className}>
      <path
        fill="currentColor"
        d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2Zm3.68 13.06a.86.86 0 0 1-.85-.13c-1.34-.9-2.86-1.35-4.5-1.35a.86.86 0 0 1 0-1.72 9.71 9.71 0 0 1 5.22 1.53.86.86 0 0 1 .13 1.67ZM10 8.5v5.19a.75.75 0 0 1-1.5 0V8.5A.75.75 0 0 1 10 8.5Z"
      />
    </svg>
  );
}

export function AmazonMusicIcon({ className }) {
  return (
    <svg {...base} className={className}>
      <path
        fill="currentColor"
        d="M18.7 15.3c-2 1.5-4.9 2.3-7.4 2.3-3.5 0-6.6-1.3-9-3.4-.2-.2 0-.4.2-.3 2.5 1.5 5.7 2.3 8.9 2.3 2.1 0 4.4-.4 6.6-1.3.3-.1.6.2.3.4Zm.9-1c-.3-.3-1.7-.2-2.4-.1-.2 0-.3-.1-.1-.3 1.1-.8 2.9-.5 3.1-.3.2.2-.1 2-1.1 2.9-.2.1-.3.1-.2-.1.1-.4.6-1.7.4-2.1Z"
      />
      <path
        fill="currentColor"
        d="M12 6.9a3 3 0 0 0-3 2.6c0 .2.1.3.3.3l1.3.1c.2 0 .3-.1.3-.3.2-.7.7-1 1.1-1 .8 0 .9.7.9 1.6v.5c-.9 0-2.1 0-2.9.4a2.6 2.6 0 0 0-1.6 2.4c0 1.7 1.4 2.7 2.9 2.7 1.3 0 2-.3 2.9-1.2a1.9 1.9 0 0 0 .4.5c.1.1.3.1.4 0l.9-.8c.1-.1.1-.3 0-.4a2 2 0 0 1-.5-1.5V9.6c0-2-1.4-2.7-3.4-2.7Zm.9 3.9v.3c0 .6 0 1.1-.3 1.6a1.2 1.2 0 0 1-1 .7c-.6 0-.9-.4-.9-1s.4-1.1.9-1.4c.3-.2.7-.2 1.3-.2Z"
      />
    </svg>
  );
}
