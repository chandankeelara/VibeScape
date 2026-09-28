/** header.site-nav — frontend/login.html:113-124. */

import { memo } from 'react';

import { Brand } from './bits';
import { GITHUB_URL } from './links';
import styles from './SiteNav.module.css';

function SiteNav() {
  return (
    <header className={styles.nav}>
      <a className={styles.brandLink} href="#top" aria-label="VibeScape — top of page">
        <Brand nameClassName={styles.brandName} />
      </a>
      <nav className={styles.links} aria-label="Landing page sections">
        <a href="#features">Features</a>
        <a href="#mobile">Mobile</a>
        <a href="#faq">FAQ</a>
        <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
          GitHub
        </a>
      </nav>
    </header>
  );
}

export default memo(SiteNav);
