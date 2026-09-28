/** footer.site-foot — frontend/login.html:479-493. Legacy filled #year from JS. */

import { memo } from 'react';

import { Brand } from './bits';
import { GITHUB_URL } from './links';
import styles from './SiteFoot.module.css';

function SiteFoot() {
  return (
    <footer className={styles.foot}>
      <div className={styles.inner}>
        <a href="#top" aria-label="VibeScape — top of page">
          <Brand className={styles.brand} markClassName={styles.mark} />
        </a>
        <div className={styles.meta}>
          <span>© {new Date().getFullYear()}</span>
          <span className={styles.sep} aria-hidden="true">
            ·
          </span>
          <span>
            built by{' '}
            <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
              Chandan Keelara
            </a>
          </span>
          <span className={styles.sep} aria-hidden="true">
            ·
          </span>
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            source
          </a>
        </div>
      </div>
    </footer>
  );
}

export default memo(SiteFoot);
