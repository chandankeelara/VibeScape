/**
 * The queue panel: Now playing, Up next, Recommended for this track.
 *
 * One element, three presentations (S2), chosen by PlayerPage from the
 * viewport and passed in as `variant`:
 *   panel  — >=1100px, a column beside the now-playing panel; `open` hides it
 *            (the bar's queue button), the grid reflows.
 *   drawer — 768-1099px, slides in from the right over a scrim.
 *   sheet  — <=767px, slides up from the bottom over a scrim.
 *
 * State (queue ops, DJ, recommendations) lives in QueueStateProvider so the
 * phone home list can show the same lists without forking useDj.
 *
 * The queue is user-managed only and never auto-fills: it grows from search
 * "+" buttons and from the recommendations. DJ mode changes what the
 * recommendations *are*, never what the queue contains.
 */

import { useEffect, useRef } from 'react';
import { usePlayer } from '../../state/PlayerContext';
import { trackKey } from '../../lib/vibe';
import Icon from '../../ui/Icon';
import Bit from '../../ui/Bit';
import ui from '../../ui/controls.module.css';
import QueueRow from './QueueRow';
import { useQueueState } from './QueueState';
import { useQueueDrag } from './useQueueDrag';
import styles from './QueueSidebar.module.css';

export default function QueueSidebar({ variant = 'panel', open = true, onClose }) {
  const { queue, clearQueue, reorderQueue, current, vibe } = usePlayer();
  const {
    djEnabled, djToggle, recs, recsLoading, hasAnchor, consumingKey,
    jumpTo, removeAt, playRec, addRec, insertAt,
  } = useQueueState();

  const panelRef = useRef(null);
  const overlay = variant !== 'panel';

  const { listRef, emptyRef, dragging } = useQueueDrag({
    queue,
    recs,
    onReorder: reorderQueue,
    onInsert: insertAt,
    styles,
  });

  // Overlay variants: Escape closes, focus moves in on open.
  useEffect(() => {
    if (!overlay || !open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    document.addEventListener('keydown', onKey);
    panelRef.current?.querySelector('h2')?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [overlay, open, onClose]);

  const cls = [
    styles.queue,
    styles[variant],
    open ? styles.open : '',
    dragging ? styles.dragActive : '',
  ].filter(Boolean).join(' ');

  return (
    <>
      <aside
        ref={panelRef}
        className={cls}
        aria-labelledby="queue-heading"
        // A closed drawer/sheet/panel drops out of the tab order and the AT
        // tree entirely — `inert`, not just aria-hidden.
        inert={!open}
      >
        {variant === 'sheet' && <div className={styles.grab} aria-hidden="true"><i /></div>}

        <div className={styles.head}>
          <h2 id="queue-heading" className={styles.heading} tabIndex={-1}>Queue</h2>
          <div className={styles.headRight}>
            <button
              type="button"
              className={ui.switch}
              role="switch"
              aria-checked={djEnabled}
              onClick={djToggle}
              title="DJ mode — picks shaped by what you play, skip and add"
            >
              <span>DJ mode</span>
              <span className={ui.switchTrack} aria-hidden="true" />
            </button>
            {overlay && (
              <button type="button" className={ui.ib} onClick={onClose} aria-label="Close queue">
                <Icon name="x" />
              </button>
            )}
          </div>
        </div>

        <div className={styles.scroll}>
          {current && (
            <section className={styles.sec} aria-labelledby="q-now">
              <h3 id="q-now" className={styles.secTitle}>Now playing</h3>
              <ul className={styles.list}>
                <QueueRow track={current} kind="now" />
              </ul>
            </section>
          )}

          <section className={styles.sec} aria-labelledby="q-up">
            <h3 id="q-up" className={styles.secTitle}>
              <span>Up next</span>
              <span className={styles.secRight}>
                {queue.length > 0 && (
                  <>
                    <span className={styles.count}>{queue.length} track{queue.length === 1 ? '' : 's'}</span>
                    <button type="button" className={styles.clear} onClick={clearQueue}>Clear</button>
                  </>
                )}
              </span>
            </h3>
            {queue.length === 0 ? (
              <div className={styles.empty} ref={emptyRef}>
                <b>Your queue is empty</b>
                Add songs from search, or drag a recommendation here.
              </div>
            ) : (
              <ol className={styles.list} ref={listRef}>
                {queue.map((t, i) => (
                  <QueueRow
                    key={`${trackKey(t)}:${i}`}
                    track={t}
                    index={i}
                    kind="queue"
                    onActivate={jumpTo}
                    onAction={removeAt}
                    onMove={reorderQueue}
                  />
                ))}
              </ol>
            )}
          </section>

          <section className={styles.sec} aria-labelledby="q-recs">
            <h3 id="q-recs" className={styles.secTitle}>Recommended for this track</h3>
            <p className={styles.secSub}>
              {djEnabled
                ? 'Shaped by what you play, skip and add'
                : `Picked by sound, near vibe ${vibe}`}
            </p>
            {recsLoading ? (
              <p className={styles.loading}>
                <Bit thinking reactive={false} className={styles.loadingBit} />
                {djEnabled ? 'DJ is picking…' : 'Finding similar songs…'}
              </p>
            ) : recs.length === 0 ? (
              <p className={styles.note}>
                {hasAnchor
                  ? 'No similar tracks in your library yet.'
                  : 'Play a track to see similar songs from your library.'}
              </p>
            ) : (
              <ul className={styles.list}>
                {recs.map((t, i) => (
                  <QueueRow
                    key={trackKey(t)}
                    track={t}
                    index={i}
                    kind="rec"
                    onActivate={playRec}
                    onAction={addRec}
                    consuming={consumingKey === trackKey(t)}
                  />
                ))}
              </ul>
            )}
          </section>
        </div>
      </aside>

      {overlay && (
        <button
          type="button"
          className={`${styles.scrim} ${open ? styles.scrimOn : ''}`}
          onClick={onClose}
          tabIndex={-1}
          aria-hidden="true"
        />
      )}
    </>
  );
}
