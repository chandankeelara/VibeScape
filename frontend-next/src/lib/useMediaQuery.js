import { useEffect, useState } from 'react';

/**
 * Live boolean for a media query. Subscribes to `change`, so it fires on
 * crossing the breakpoint, not on every resize tick.
 */
export default function useMediaQuery(query) {
  const [matches, setMatches] = useState(
    () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : false),
  );
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = (e) => setMatches(e.matches);
    // Re-read once: the breakpoint can be crossed between first render and
    // this effect (and StrictMode's double-mount makes that observable).
    setMatches(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [query]);
  return matches;
}

/** The S2 breakpoints. Must match the @media rules in the player modules. */
export const PHONE = '(max-width: 767px)';
export const WIDE = '(min-width: 1100px)';
/** Below theater's floor (useTheaterMode's 1024px): lyrics become a sheet. */
export const BELOW_THEATER = '(max-width: 1023px)';
