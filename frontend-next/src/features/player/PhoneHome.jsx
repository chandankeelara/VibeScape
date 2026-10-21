import { usePlayer } from '../../state/PlayerContext';
import { trackKey } from '../../lib/vibe';
import { QueueRow, useQueueState } from '../queue';
import ui from '../../ui/controls.module.css';
import RecentTrail from './RecentTrail';
import DjLine from './DjLine';
import styles from './PhoneHome.module.css';

/**
 * Phone only: the home list behind the mini-player (S2). Recently played as
 * shortcuts, then Up next and For this track — the same lists the queue sheet
 * shows, read from the same QueueStateProvider, so there is one DJ.
 *
 * Hidden above 767px by its own CSS; on desktop the now-playing panel and
 * the queue column carry all of this.
 *
 * The DJ line heads the list, with Bit: the home screen is where a phone
 * spends most of a session, and without it he would only exist behind a tap.
 * `bitPaused` freezes this copy while it is out of view: the full-screen
 * player covers it, or the page is wider than a phone and it is display:none.
 */
export default function PhoneHome({ bitPaused = false }) {
  const { queue, reorderQueue, current } = usePlayer();
  const {
    recs, recsLoading, jumpTo, removeAt, playRec, addRec, consumingKey, discover,
  } = useQueueState();

  return (
    <section className={styles.home} aria-label="Home">
      <DjLine className={styles.dj} bitPaused={bitPaused} />
      <RecentTrail variant="shortcuts" className={styles.block} />

      <div className={styles.block}>
        <div className={ui.secHead}>
          <h2 className={ui.secTitle}>Up next</h2>
          {queue.length > 0 && <span className={ui.secAside}>{queue.length} track{queue.length === 1 ? '' : 's'}</span>}
        </div>
        {queue.length === 0 ? (
          <p className={styles.empty}>Nothing queued. Add songs from search or from the picks below.</p>
        ) : (
          <ol className={styles.list}>
            {queue.map((t, i) => (
              <QueueRow
                key={`${trackKey(t)}:${i}`}
                track={t}
                index={i}
                kind="queue"
                draggable={false}
                onActivate={jumpTo}
                onAction={removeAt}
                onMove={reorderQueue}
              />
            ))}
          </ol>
        )}
      </div>

      {current && (
        <div className={styles.block}>
          <div className={ui.secHead}>
            <h2 className={ui.secTitle}>For this track</h2>
            <span className={ui.secAside}>{discover ? 'by sound · discovery' : 'by sound'}</span>
          </div>
          {recsLoading ? (
            <p className={styles.empty}>Finding similar songs…</p>
          ) : recs.length === 0 ? (
            <p className={styles.empty}>
              {discover ? 'No similar tracks found in the catalogue.' : 'No similar tracks in your library yet.'}
            </p>
          ) : (
            <ul className={styles.list}>
              {recs.map((t, i) => (
                <QueueRow
                  key={trackKey(t)}
                  track={t}
                  index={i}
                  kind="rec"
                  draggable={false}
                  onActivate={playRec}
                  onAction={addRec}
                  consuming={consumingKey === trackKey(t)}
                />
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
