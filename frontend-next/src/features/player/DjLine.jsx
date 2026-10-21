import { Suspense, lazy } from 'react';
import { usePlayer } from '../../state/PlayerContext';
import { useTheme } from '../../state/ThemeContext';
import { useQueueState } from '../queue';
import Bit from '../../ui/Bit';
import styles from './DjLine.module.css';

/*
 * The full rig and its ~105KB of animation CSS, off the main chunk. The slot
 * below reserves his box, so the line does not move when it arrives.
 */
const BitDock = lazy(() => import('./bit/BitDock'));

/**
 * The DJ explaining itself in one quiet line, with Bit beside it.
 *
 * Reads startInfo from PlayerContext — WHY the current track started, which
 * is a fact the client already knows (how loadTrack was called). Nothing here
 * weighs or scores anything: the DJ's reasoning stays in backend/dj_replay.py.
 * The line only names the event that led to the pick.
 *
 * Bit stands here in every layout that shows the line: under the vibe card
 * (desktop, video, theater's lower column, the phone's full-screen player)
 * and at the top of the phone's home list. Never on the video, never in the
 * lyrics: where lyrics take the side column they take the line with them.
 *
 * `bitPaused` freezes him in a copy that is mounted but not in view.
 *
 * Bit's size is the account menu's setting. Medium is the rig here. Large is
 * the rig at the end of the panel's foot row, after the recent tiles, and
 * `bigElsewhere` says that copy is actually mounted, so the line keeps the
 * small icon beside the sentence and the two are never the same size twice.
 * On the phone there is no foot row and Large falls back to Medium.
 */
function sentence({ kind, from }, djOn, mood) {
  if (kind === 'search') return djOn ? <>Playing from search. The DJ takes it from here</> : <>Playing from search</>;
  if (kind === 'queue') return <>From your Up next</>;
  if (kind === 'recent') return <>Back to one you played earlier</>;
  if (kind === 'resume') return <>Picking up where you left off</>;
  if (kind === 'pick') return from ? <>Close in sound to <b>{from}</b></> : <>Close in sound</>;
  if (!djOn) return <>DJ is off. Playing near your vibe</>;
  if (kind === 'ended') return from ? <>Because you played <b>{from}</b></> : <>Picked for your vibe</>;
  if (kind === 'skip') return <>Steering away after a skip</>;
  return <>Easing toward <b className={styles.cap}>{mood}</b></>;
}

export default function DjLine({ className = '', bitPaused = false, bigElsewhere = false }) {
  const { startInfo, mood, loadingTrack, current } = usePlayer();
  const { djEnabled } = useQueueState();
  const { bitSize } = useTheme();

  if (!current && !loadingTrack) return null;

  const icon = bitSize === 'large' && bigElsewhere;
  const rig = bitSize !== 'off' && !icon;

  return (
    <div className={`${styles.line} ${rig ? styles.withBit : ''} ${className}`}>
      {rig && (
        <span className={styles.bit}>
          <Suspense fallback={null}><BitDock paused={bitPaused} /></Suspense>
        </span>
      )}
      {icon && <span className={styles.icon}><Bit asleep={!djEnabled} /></span>}
      <p className={styles.say} aria-live="polite">
        {djEnabled && <span className={styles.tag}>DJ</span>}
        {loadingTrack ? <>Picking the next track…</> : sentence(startInfo, djEnabled, mood)}
      </p>
    </div>
  );
}
