import { memo, useCallback, useEffect, useRef, useState } from 'react';
import Icon from '../../ui/Icon';
import ui from '../../ui/controls.module.css';
import { useActiveLine, useLyrics } from './LyricsContext';
import SyncBar from './SyncBar';
import styles from './LyricsPanel.module.css';

/**
 * The lyrics, in every layout but theater's subtitle strip.
 *
 *   stage  audio view: replaces the artwork, on a blurred copy of it
 *   sheet  < 1024px: the body of the full-screen sheet (LyricsSheet)
 *   side   video view: a scrolling list in the meta column, never on the video
 *   dock   theater: the column under the video. When synced, the words are on
 *          the video (LyricsStrip) and this holds only the sync bar.
 *
 * stage and sheet sit on an always-dark backdrop (like the video surface) in
 * both themes; side and dock sit on the panel and follow the theme.
 */

/** A manual scroll holds auto-scroll off for this long. */
const MANUAL_SCROLL_HOLD_MS = 4000;

const reducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** An empty LRC line is an instrumental break. */
const lineText = (text) => (text && text.trim() ? text : '♪');

export default function LyricsPanel({ variant = 'side', className = '' }) {
  const { track, view, credit, retry, startManualSync, canManualSync } = useLyrics();
  const dark = variant === 'stage' || variant === 'sheet';
  const big = variant === 'stage' || variant === 'sheet';
  // In theater the synced words are on the video; this column keeps the bar.
  const wordsElsewhere = variant === 'dock' && view.kind === 'synced';

  const cls = [
    styles.panel,
    styles[variant],
    dark ? styles.dark : styles.themed,
    className,
  ].filter(Boolean).join(' ');

  return (
    <section className={cls} aria-label="Lyrics">
      {dark && <Backdrop url={track?.artwork_url} />}

      {!wordsElsewhere && (
        <div className={styles.body}>
          <Body view={view} big={big} onRetry={retry} />
        </div>
      )}

      <footer className={styles.foot}>
        {wordsElsewhere && <p className={styles.note}>Lyrics are on the video.</p>}
        {view.kind === 'preview' && <p className={styles.note}>Lyrics sync on full playback</p>}
        {view.kind === 'mismatch' && (
          <div className={styles.noteRow}>
            <p className={styles.note}>This video runs a different length, so timings may be off.</p>
            {canManualSync && (
              <button type="button" className={styles.pill} onClick={startManualSync}>
                <Icon name="sync" size={16} />Sync manually
              </button>
            )}
          </div>
        )}
        {view.kind === 'synced' && <SyncBar tone={dark ? 'dark' : 'themed'} />}
        <p className={styles.credit}>{credit}</p>
      </footer>
    </section>
  );
}

function Backdrop({ url }) {
  return (
    <div className={styles.backdrop} aria-hidden="true">
      {url && <img src={url} alt="" />}
    </div>
  );
}

function Body({ view, big, onRetry }) {
  switch (view.kind) {
    case 'loading':
      return (
        <Status>
          <span className={ui.dots} aria-hidden="true"><i /><i /><i /></span>
          <span>Loading lyrics</span>
        </Status>
      );
    case 'synced':
      return <SyncedLines lines={view.lines} big={big} />;
    case 'plain':
    case 'preview':
    case 'mismatch':
      return <PlainLines lines={view.plain} big={big} />;
    case 'instrumental':
      return <Status icon="music" title="Instrumental" />;
    case 'error':
      return (
        <Status title="Lyrics unavailable right now">
          <button type="button" className={styles.pill} onClick={onRetry}>Try again</button>
        </Status>
      );
    case 'none':
    default:
      return <Status icon="lyrics" title="No lyrics for this song yet" />;
  }
}

function Status({ icon, title, children }) {
  return (
    <div className={styles.status} role="status">
      {icon && <Icon name={icon} size={28} />}
      {title && <p className={styles.statusTitle}>{title}</p>}
      {children}
    </div>
  );
}

function PlainLines({ lines, big }) {
  return (
    <div className={`${styles.scroller} ${big ? styles.big : ''}`} tabIndex={0} aria-label="Lyrics text">
      <div className={styles.plain}>
        {lines.map((l, i) => (
          // Static text in a fixed order: the index is the identity.
          <p key={i} className={l.trim() ? styles.plainLine : styles.gap}>{l.trim() ? l : ' '}</p>
        ))}
      </div>
    </div>
  );
}

/**
 * Synced lines: the active one large and bright, auto-scrolled to the centre.
 *
 * Only this component subscribes to the active line, and each row is memoised
 * on (text, its relation to the active line, tab stop) — so a line change
 * re-renders the list once and four or five rows, not every row.
 *
 * Keyboard: one tab stop (roving tabindex); Up / Down move between lines,
 * Enter seeks. Those keys are stopped here so the player's global arrow-key
 * shortcuts (vibe ±5) do not also fire.
 */
function SyncedLines({ lines, big }) {
  const { canSeek, seekToLine } = useLyrics();
  const active = useActiveLine();
  const listRef = useRef(null);
  const holdUntil = useRef(0);
  const resumeTimer = useRef(0);
  const activeRef = useRef(active);
  const [focusIdx, setFocusIdx] = useState(null);
  const [said, setSaid] = useState('');

  const centre = useCallback((i, smooth = true) => {
    const list = listRef.current;
    if (!list) return;
    const row = i >= 0 ? list.querySelector(`[data-i="${i}"]`) : null;
    const top = row ? row.offsetTop - (list.clientHeight - row.offsetHeight) / 2 : 0;
    list.scrollTo({ top: Math.max(0, top), behavior: smooth && !reducedMotion() ? 'smooth' : 'auto' });
  }, []);

  useEffect(() => {
    activeRef.current = active;
    if (performance.now() < holdUntil.current) return;
    centre(active);
  }, [active, centre]);

  // First paint: jump, do not glide in from the top. And say where we are,
  // once — announcing every line would talk over the song.
  useEffect(() => {
    centre(activeRef.current, false);
    const line = lines[Math.max(0, activeRef.current)];
    if (line) setSaid(`Lyrics. ${lineText(line.text)}`);
  }, [lines, centre]);

  useEffect(() => () => clearTimeout(resumeTimer.current), []);

  /** The user scrolled: leave the list where they put it, for a while. */
  const hold = useCallback(() => {
    holdUntil.current = performance.now() + MANUAL_SCROLL_HOLD_MS;
    clearTimeout(resumeTimer.current);
    resumeTimer.current = setTimeout(() => {
      holdUntil.current = 0;
      centre(activeRef.current);
    }, MANUAL_SCROLL_HOLD_MS);
  }, [centre]);

  const onSeek = useCallback((i) => {
    holdUntil.current = 0;
    clearTimeout(resumeTimer.current);
    setFocusIdx(i);
    seekToLine(i);
    setSaid(lineText(lines[i]?.text));
  }, [seekToLine, lines]);

  const onLineKey = useCallback((e, i) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      e.stopPropagation();
      const next = Math.max(0, Math.min(lines.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)));
      setFocusIdx(next);
      hold();
      listRef.current?.querySelector(`[data-i="${next}"] button`)?.focus();
    } else if (e.key === 'Enter' || e.key === ' ') {
      // Stop the global space = play/pause; the button's own click seeks.
      e.stopPropagation();
    }
  }, [lines.length, hold]);

  const tabStop = focusIdx ?? Math.max(0, active);

  return (
    <>
      <ol
        ref={listRef}
        className={`${styles.scroller} ${styles.synced} ${big ? styles.big : ''}`}
        onWheel={hold}
        onTouchMove={hold}
        onPointerDown={(e) => { if (e.target === listRef.current) hold(); }}
        onBlur={(e) => { if (!listRef.current?.contains(e.relatedTarget)) setFocusIdx(null); }}
      >
        {lines.map((l, i) => (
          <Line
            key={i}
            i={i}
            text={l.text}
            rel={relation(i, active)}
            tabbable={i === tabStop}
            canSeek={canSeek}
            onSeek={onSeek}
            onKey={onLineKey}
          />
        ))}
      </ol>
      <p className={ui.srOnly} aria-live="polite">{said}</p>
    </>
  );
}

function relation(i, active) {
  const d = i - active;
  if (d === 0) return 'cur';
  if (d === -1) return 'prev';
  if (d === 1 || d === 2) return 'next';
  return d < 0 ? 'past' : 'far';
}

const Line = memo(function Line({ i, text, rel, tabbable, canSeek, onSeek, onKey }) {
  const cls = `${styles.line} ${styles[rel]}`;
  const content = lineText(text);
  return (
    <li data-i={i} className={cls}>
      {canSeek ? (
        <button
          type="button"
          className={styles.lineBtn}
          tabIndex={tabbable ? 0 : -1}
          aria-current={rel === 'cur' ? 'true' : undefined}
          onClick={() => onSeek(i)}
          onKeyDown={(e) => onKey(e, i)}
        >
          {content}
        </button>
      ) : (
        <span className={styles.lineBtn} aria-current={rel === 'cur' ? 'true' : undefined}>{content}</span>
      )}
    </li>
  );
});
