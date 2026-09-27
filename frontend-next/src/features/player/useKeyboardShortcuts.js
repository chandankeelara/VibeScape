import { useEffect } from 'react';
import { usePlayer } from '../../state/PlayerContext';

/**
 * Global keyboard shortcuts. Ported from the handlers around
 * frontend/app.js:2728 and documented in index.html's help popover:
 *
 *   space        play / pause
 *   ← →          previous / next track
 *   ↑ ↓          shift vibe ±5
 *   Home / End   seek 0 / 100%
 *   PgUp / PgDn  seek ±15s
 *
 * Ctrl+K (search focus) is owned by the search feature.
 */
export default function useKeyboardShortcuts({ onToggleMetrics, onToggleHelp } = {}) {
  const { togglePlay, next, prev, shiftVibe, seek, fetchForVibe } = usePlayer();

  useEffect(() => {
    const onKeyDown = (e) => {
      // Never hijack typing.
      const el = e.target;
      const tag = el?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el?.isContentEditable) return;
      if (e.metaKey || e.altKey) return;

      if (e.ctrlKey && e.shiftKey) {
        if (e.key.toLowerCase() === 'm') { e.preventDefault(); onToggleMetrics?.(); }
        return;
      }
      if (e.ctrlKey) return;

      switch (e.key) {
        case ' ':
        case 'Spacebar':
          e.preventDefault();
          togglePlay();
          break;
        case 'ArrowRight': e.preventDefault(); next(); break;
        case 'ArrowLeft':  e.preventDefault(); prev(); break;
        case 'ArrowUp':    e.preventDefault(); shiftVibe(5);  fetchForVibe(); break;
        case 'ArrowDown':  e.preventDefault(); shiftVibe(-5); fetchForVibe(); break;
        case 'Home':       e.preventDefault(); seek(0); break;
        case 'End':        e.preventDefault(); seek(1); break;
        case '?':          e.preventDefault(); onToggleHelp?.(); break;
        default: break;
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [togglePlay, next, prev, shiftVibe, seek, fetchForVibe, onToggleMetrics, onToggleHelp]);
}
