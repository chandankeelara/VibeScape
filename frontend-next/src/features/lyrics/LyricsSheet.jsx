import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import Icon from '../../ui/Icon';
import ui from '../../ui/controls.module.css';
import { useLyrics } from './LyricsContext';
import LyricsPanel from './LyricsPanel';
import styles from './LyricsSheet.module.css';

/**
 * Below 1024px: the lyrics as a full-screen sheet over the player, laid out
 * like the audio view.
 *
 * Portalled to <body> so it is not trapped in the now-playing panel's
 * stacking context. Nothing is moved: the panel (and the YouTube iframe in it)
 * stays where it is underneath, still playing. Closing the sheet turns lyrics
 * off — the sheet IS the lyrics on a narrow screen.
 */
export default function LyricsSheet() {
  const { track, close } = useLyrics();
  const ref = useRef(null);
  const closeRef = useRef(null);

  useEffect(() => {
    const opener = document.activeElement;
    closeRef.current?.focus();
    return () => {
      if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
    };
  }, []);

  const onKeyDown = (e) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      close();
      return;
    }
    if (e.key !== 'Tab') return;
    // Keep focus inside while the sheet is up.
    const focusable = ref.current?.querySelectorAll(
      'button:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  return createPortal(
    <div ref={ref} className={styles.sheet} role="dialog" aria-modal="true" aria-label="Lyrics" onKeyDown={onKeyDown}>
      <header className={styles.head}>
        <button ref={closeRef} type="button" className={`${ui.ib} ${styles.close}`} onClick={close} aria-label="Close lyrics">
          <Icon name="down" />
        </button>
        <div className={styles.title}>
          <b>{track?.title || 'Untitled'}</b>
          <span>{track?.artist || 'Unknown artist'}</span>
        </div>
      </header>
      <LyricsPanel variant="sheet" className={styles.panel} />
    </div>,
    document.body,
  );
}
