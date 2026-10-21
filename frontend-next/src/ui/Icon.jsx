/**
 * The S2 icon set — one stroke family, 2px, round joins (Lucide-style),
 * copied from the sprite in design/premium-player/s2-familiar.html.
 *
 * Inline <svg> per use rather than a <use href="#sprite"> sheet: the PiP
 * miniplayer renders into a second document, where a sprite in this one does
 * not resolve.
 */

const PATHS = {
  play: <path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.4-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z" fill="currentColor" stroke="none" />,
  pause: (
    <>
      <rect x="5.5" y="4" width="4.5" height="16" rx="1.3" fill="currentColor" stroke="none" />
      <rect x="14" y="4" width="4.5" height="16" rx="1.3" fill="currentColor" stroke="none" />
    </>
  ),
  prev: (
    <>
      <path d="M6 5v14" />
      <path d="M19 5.5v13a.6.6 0 0 1-.92.5L9 12.5a.6.6 0 0 1 0-1L18.08 5a.6.6 0 0 1 .92.5z" fill="currentColor" />
    </>
  ),
  next: (
    <>
      <path d="M18 5v14" />
      <path d="M5 5.5v13a.6.6 0 0 0 .92.5L15 12.5a.6.6 0 0 0 0-1L5.92 5A.6.6 0 0 0 5 5.5z" fill="currentColor" />
    </>
  ),
  pip: (
    <>
      <rect x="2.5" y="4.5" width="19" height="15" rx="2.5" />
      <rect x="12" y="11.5" width="7" height="5" rx="1" fill="currentColor" stroke="none" />
    </>
  ),
  theater: <rect x="2" y="6" width="20" height="12" rx="2" />,
  normal: <rect x="5" y="6" width="14" height="12" rx="2" />,
  queue: (
    <>
      <path d="M3 6h13M3 12h13M3 18h8" />
      <path d="M19 13v6a1.8 1.8 0 1 1-1.8-1.8H19" />
      <path d="M19 13l2.5-1" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </>
  ),
  help: (
    <>
      <circle cx="12" cy="12" r="9.5" />
      <path d="M9.5 9.2a2.6 2.6 0 0 1 5 .9c0 1.7-2.5 2.3-2.5 3.9" />
      <path d="M12 17.2h.01" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>
  ),
  moon: <path d="M20.5 14.5A8.5 8.5 0 1 1 9.5 3.5a7 7 0 0 0 11 11z" />,
  down: <path d="m6 9 6 6 6-6" />,
  up: <path d="m6 15 6-6 6 6" />,
  back: <path d="M19 12H5M11 18l-6-6 6-6" />,
  plus: <path d="M12 5v14M5 12h14" />,
  x: <path d="M18 6 6 18M6 6l12 12" />,
  grip: (
    <>
      {[[9, 6], [15, 6], [9, 12], [15, 12], [9, 18], [15, 18]].map(([cx, cy]) => (
        <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="1.4" fill="currentColor" stroke="none" />
      ))}
    </>
  ),
  check: <path d="M20 6 9 17l-5-5" />,
  verify: (
    <>
      <path d="M3 14v-2a9 9 0 0 1 18 0v2" />
      <path d="M21 15a2 2 0 0 1-2 2h-1v-6h1a2 2 0 0 1 2 2zM3 15a2 2 0 0 0 2 2h1v-6H5a2 2 0 0 0-2 2z" />
    </>
  ),
  chart: (
    <>
      <path d="M3 3v18h18" />
      <path d="m7 15 4-4 3 3 5-6" />
    </>
  ),
  vol: (
    <>
      <path d="M11 5 6 9H3v6h3l5 4z" fill="currentColor" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" />
    </>
  ),
  volLow: (
    <>
      <path d="M11 5 6 9H3v6h3l5 4z" fill="currentColor" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7" />
    </>
  ),
  mute: (
    <>
      <path d="M11 5 6 9H3v6h3l5 4z" fill="currentColor" />
      <path d="m22 9-6 6M16 9l6 6" />
    </>
  ),
  music: (
    <>
      <path d="M9 18V5l12-2v13" />
      <circle cx="6" cy="18" r="3" />
      <circle cx="18" cy="16" r="3" />
    </>
  ),
  video: (
    <>
      <rect x="2" y="5" width="15" height="14" rx="2.5" />
      <path d="m17 10 5-3v10l-5-3" />
    </>
  ),
  novideo: (
    <>
      <path d="M10.7 5H14.5A2.5 2.5 0 0 1 17 7.5v3.3l5-3V17M17 17a2.5 2.5 0 0 1-2.5 2H4.5A2.5 2.5 0 0 1 2 16.5v-9A2.5 2.5 0 0 1 4.5 5" />
      <path d="m2 2 20 20" />
    </>
  ),
  // A vocal mic, not lines + a note: that is too close to `queue`.
  lyrics: (
    <>
      <path d="m11 7.6-6 8.19a1 1 0 0 0 .1 1.3l.82.82a1 1 0 0 0 1.31.09L15.09 12" />
      <path d="M16.5 21.17C15.5 20.5 14.37 20 13 20c-2.06 0-3.93 2.36-6 2-2.07-.36-2.78-3.37-1.5-4.5" />
      <circle cx="16" cy="7" r="5" />
    </>
  ),
  logo: <path d="M3 15c2.5 0 2.5-6 5-6s2.5 9 5 9 2.5-12 5-12 2 6 3 6" strokeWidth="2.6" />,
  out: <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />,
  sync: (
    <>
      <path d="M21 4v6h-6M3 20v-6h6" />
      <path d="M20.5 10A8.5 8.5 0 0 0 5.6 6.4L3 9M3.5 14a8.5 8.5 0 0 0 14.9 3.6L21 15" />
    </>
  ),
  link: (
    <>
      <path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.5 1.5" />
      <path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.5-1.5" />
    </>
  ),
  shield: <path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6z" />,
  trash: (
    <>
      <path d="M3 6h18" />
      <path d="M19 6l-1.4 13.2a2 2 0 0 1-2 1.8H8.4a2 2 0 0 1-2-1.8L5 6M9 6V4h6v2" />
    </>
  ),
  // One outline, filled or not: the state is the shape as well as the colour.
  heart: <path d="M12 20.5s-7.5-4.6-9.2-9.4C1.6 7.6 3.9 4 7.4 4c2 0 3.5 1.1 4.6 2.7C13.1 5.1 14.6 4 16.6 4c3.5 0 5.8 3.6 4.6 7.1-1.7 4.8-9.2 9.4-9.2 9.4z" />,
  heartFill: <path d="M12 20.5s-7.5-4.6-9.2-9.4C1.6 7.6 3.9 4 7.4 4c2 0 3.5 1.1 4.6 2.7C13.1 5.1 14.6 4 16.6 4c3.5 0 5.8 3.6 4.6 7.1-1.7 4.8-9.2 9.4-9.2 9.4z" fill="currentColor" />,
  disc: (
    <>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
};

/**
 * <Icon name="play" size={20} />. Decorative by default (aria-hidden); give
 * the BUTTON the accessible name, not the glyph.
 */
export default function Icon({ name, size = 20, className = '', title }) {
  const body = PATHS[name];
  if (!body) return null;
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : 'true'}
      role={title ? 'img' : undefined}
      focusable="false"
      style={{ flex: 'none', display: 'block' }}
    >
      {title && <title>{title}</title>}
      {body}
    </svg>
  );
}

/** Spotify's mark. `wave` is the colour of the three arcs (the page bg on a chip, ink on the green button). */
export function SpotifyMark({ size = 16, wave = 'var(--spotify-ink)', color = 'currentColor' }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false" style={{ flex: 'none', display: 'block' }}>
      <circle cx="12" cy="12" r="10" fill={color} />
      <path
        d="M7 9.5c3.5-1 7.5-.6 10.5 1M7.6 12.6c2.9-.8 6-.5 8.4.9M8.2 15.5c2.2-.6 4.5-.4 6.4.7"
        fill="none" stroke={wave} strokeWidth="1.7" strokeLinecap="round"
      />
    </svg>
  );
}
