/**
 * Frontend unit tests live in ../tests/frontend (one test tree for the whole
 * repo — see tests/README.md). This config sits here because vitest and the
 * app's dependencies resolve from this package's node_modules.
 *
 * `globals: true` is load-bearing: test files outside this package cannot
 * resolve a bare `import ... from 'vitest'`, so describe / it / expect / vi
 * come in as globals instead.
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

const here = path.dirname(fileURLToPath(import.meta.url));
const testsDir = path.resolve(here, '../tests/frontend');

export default defineConfig({
  test: {
    dir: testsDir,
    include: ['**/*.test.js'],
    environment: 'node',
    globals: true,
    setupFiles: [path.join(testsDir, 'setup.js')],
    restoreMocks: true,
  },
  server: { fs: { allow: [path.resolve(here, '..')] } },
});
