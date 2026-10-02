import { useEffect, useState } from 'react';
import { useSyncJob } from './SyncJobProvider';
import styles from './SyncPill.module.css';

/**
 * What the import collapses into once music is playing.
 *
 * The modal steps out of the way as soon as the first track is playable, so
 * something has to carry the job afterwards — otherwise work is happening
 * that the user has no way to see or stop. This is that something: small,
 * fixed, and ignorable.
 *
 * Deliberately NOT a toast. A toast implies an event that has finished; this
 * is a process that is still running and that the user may want to act on.
 * It stays until the job ends.
 */
export default function SyncPill({ onOpen }) {
  const job = useSyncJob();
  const [dismissedDone, setDismissedDone] = useState(false);

  const phase = job ? job.phase : 'idle';

  /*
   * A finished job clears itself after a beat.
   *
   * Long enough to register that it completed, short enough that it is gone
   * before it becomes furniture. Errors do NOT auto-clear — those need a
   * decision, so they wait to be dismissed.
   */
  useEffect(() => {
    if (phase !== 'complete') return undefined;
    const t = setTimeout(() => setDismissedDone(true), 6000);
    return () => clearTimeout(t);
  }, [phase]);

  useEffect(() => {
    if (phase === 'running') setDismissedDone(false);
  }, [phase]);

  if (!job || phase === 'idle') return null;
  if (phase === 'complete' && dismissedDone) return null;

  const { progress, readyNow, summary, errorMsg } = job;
  const pct = progress.total
    ? Math.min(100, Math.round((progress.processed / progress.total) * 100))
    : 0;

  const running = phase === 'running';
  const failed = phase === 'error';

  return (
    <div
      className={`${styles.pill} ${failed ? styles.failed : ''}`}
      role="status"
      aria-live="polite"
    >
      {/* The bar is the background of the pill, not a separate element — at
          this size a discrete track-and-fill reads as clutter. */}
      {running && <span className={styles.fill} style={{ width: `${pct}%` }} aria-hidden="true" />}

      <span className={styles.body}>
        <span className={styles.label}>
          {failed ? 'Import failed' : running ? 'Importing your library' : 'Library imported'}
        </span>
        <span className={styles.detail}>
          {failed
            ? errorMsg
            : running
              ? `${readyNow} ready to play${progress.total ? ` · ${pct}%` : ''}`
              : summary}
        </span>
      </span>

      <span className={styles.actions}>
        {/* Reopening is the way back to detail and to stopping, which keeps
            this surface to one line. */}
        <button type="button" className={styles.action} onClick={onOpen}>
          {running ? 'Details' : 'Open'}
        </button>
        <button
          type="button"
          className={styles.close}
          onClick={() => (running ? setDismissedDone(true) : job.dismiss())}
          aria-label="Hide"
          title="Hide — the import keeps running"
        >
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
               strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </span>
    </div>
  );
}
