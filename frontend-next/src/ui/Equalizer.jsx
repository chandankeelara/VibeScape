import { usePlayer } from '../state/PlayerContext';
import styles from './Equalizer.module.css';

/**
 * The one "music is playing" cue: three bars, compositor-only. On pause the
 * wrapper EASES down instead of the bars freezing mid-hop.
 */
export default function Equalizer({ className = '' }) {
  const { playing, loadingTrack } = usePlayer();
  const on = playing && !loadingTrack;
  return (
    <span className={`${styles.eq} ${on ? styles.on : ''} ${className}`} aria-hidden="true">
      <i /><i /><i />
    </span>
  );
}
