import { useActiveLine, useLyrics } from './LyricsContext';
import styles from './LyricsStrip.module.css';

/**
 * Theater: two subtitle lines on the video — the current one bright, the next
 * one dim — on a translucent backdrop.
 *
 * Left of centre, so it stays clear of the bottom-right corner (YouTube's own
 * controls, and Bit's dock), and raised above YouTube's control row. It takes
 * no pointer events: anything we lay over a cross-origin iframe swallows
 * clicks that can never be handed back to it.
 *
 * Rendered inside ArtStage as a SIBLING of the video, so the #ytPlayer node is
 * never touched.
 */
export default function LyricsStrip() {
  const { view } = useLyrics();
  if (view.kind !== 'synced') return null;
  return <Strip lines={view.lines} />;
}

const text = (l) => (l ? (l.text && l.text.trim() ? l.text : '♪') : '');

function Strip({ lines }) {
  const active = useActiveLine();
  const cur = text(lines[active]);
  const next = text(lines[active + 1]);
  if (!cur && !next) return null;
  return (
    <div className={styles.strip}>
      {cur && <p key={active} className={styles.cur}>{cur}</p>}
      {next && <p className={styles.next}>{next}</p>}
    </div>
  );
}
