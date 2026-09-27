import { Link } from 'react-router-dom';
import styles from './Home.module.css';

/**
 * Placeholder shell for the React app. The player itself is the LAST thing to
 * be ported (see frontend-next/MIGRATION.md) — until then this page just
 * routes to what exists and points back at the legacy app.
 */
export default function Home() {
  return (
    <main className={styles.main}>
      <span className={styles.eyebrow}>
        <span className={styles.dot} />
        frontend-next
      </span>
      <h1 className={styles.title}>VibeScape / React</h1>
      <p className={styles.lede}>
        Migration in progress. The full player still lives in the legacy app and
        remains the one to use.
      </p>

      <div className={styles.links}>
        <Link className={styles.btn} to="/admin">Admin panel →</Link>
        <a className={styles.btnGhost} href="/">← Legacy player</a>
      </div>
    </main>
  );
}
