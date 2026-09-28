/**
 * Keyboard-shortcut popover — port of frontend/index.html:160-175.
 *
 * Static content; the only behaviour is Escape / click-outside to close,
 * which it inherits from the shared Panel shell. The shortcuts themselves are
 * owned by whoever installs the key handlers — this list only describes them,
 * so if a binding changes, change it here too.
 */

import Panel from './Panel';
import styles from './HelpPopover.module.css';

const SHORTCUTS = [
  { keys: ['space'], label: 'play / pause' },
  { keys: ['←', '→'], label: 'next track' },
  { keys: ['↑', '↓'], label: 'shift vibe ±5' },
  { keys: ['Home', 'End'], label: 'seek 0 / 100%' },
  { keys: ['PgUp', 'PgDn'], label: 'seek ±15s' },
  { keys: ['Ctrl', 'Shift', 'M'], label: 'track metrics' },
  { keys: ['Ctrl', 'Shift', 'D'], label: 'Spotify debug' },
  { keys: ['?'], label: 'toggle this panel' },
];

export default function HelpPopover({ open, onClose }) {
  return (
    <Panel open={open} onClose={onClose} title="Keyboard" id="helpPopover">
      <div className={styles.rows}>
        {SHORTCUTS.map((s) => (
          <div className={styles.row} key={s.label}>
            <span className={styles.keys}>
              {s.keys.map((k) => (
                <kbd className={styles.kbd} key={k}>
                  {k}
                </kbd>
              ))}
            </span>
            <span className={styles.label}>{s.label}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
