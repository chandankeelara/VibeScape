import { usePlayer } from '../../state/PlayerContext';
import { useTheme } from '../../state/ThemeContext';
import { useQueueState } from '../queue';
import Bit from '../../ui/Bit';
import styles from './DjLine.module.css';

/**
 * The DJ explaining itself in one quiet line, with Bit beside it.
 *
 * Reads startInfo from PlayerContext — WHY the current track started, which
 * is a fact the client already knows (how loadTrack was called). Nothing here
 * weighs or scores anything: the DJ's reasoning stays in backend/dj_replay.py.
 * The line only names the event that led to the pick.
 */
function sentence({ kind, from }, djOn, mood) {
  if (kind === 'search') return djOn ? <>Playing from search. The DJ takes it from here</> : <>Playing from search</>;
  if (kind === 'queue') return <>From your Up next</>;
  if (kind === 'recent') return <>Back to one you played earlier</>;
  if (kind === 'pick') return from ? <>Close in sound to <b>{from}</b></> : <>Close in sound</>;
  if (!djOn) return <>DJ is off. Playing near your vibe</>;
  if (kind === 'ended') return from ? <>Because you played <b>{from}</b></> : <>Picked for your vibe</>;
  if (kind === 'skip') return <>Steering away after a skip</>;
  return <>Easing toward <b className={styles.cap}>{mood}</b></>;
}

export default function DjLine({ className = '' }) {
  const { startInfo, mood, loadingTrack, current } = usePlayer();
  const { djEnabled } = useQueueState();
  const { showBit } = useTheme();

  if (!current && !loadingTrack) return null;

  return (
    <div className={`${styles.line} ${className}`}>
      {showBit && <Bit thinking={loadingTrack} />}
      <p className={styles.say} aria-live="polite">
        {djEnabled && <span className={styles.tag}>DJ</span>}
        {loadingTrack ? <>Picking the next track…</> : sentence(startInfo, djEnabled, mood)}
      </p>
    </div>
  );
}
