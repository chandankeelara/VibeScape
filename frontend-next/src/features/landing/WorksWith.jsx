/** section#works.strip — frontend/login.html:412-424. */

import { memo } from 'react';

import styles from './WorksWith.module.css';

const ITEMS = [
  'Spotify',
  'YouTube',
  'Apple Music previews',
  'Deezer',
  'Bluetooth',
  'CarPlay',
  'Android Auto',
  'PWA',
];

function WorksWith() {
  return (
    <section id="works" className={styles.strip} aria-labelledby="worksWithLabel">
      <h2 className={styles.label} id="worksWithLabel">
        Works with
      </h2>
      {/* A list, not a row of spans — it is genuinely enumerable content. */}
      <ul className={styles.row}>
        {ITEMS.map((item) => (
          <li className={styles.chip} key={item}>
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default memo(WorksWith);
