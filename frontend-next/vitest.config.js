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

// Same reason as `globals`: a bare `import 'react'` in ../tests/frontend has
// no node_modules above it, so point the packages a component test needs at
// this package's copies. The app's own files resolve them naturally.
const nm = (name) => path.join(here, 'node_modules', name);
const TEST_DEPS = ['react', 'react-dom', '@tanstack/react-query', '@testing-library/react'];

export default defineConfig({
  test: {
    dir: testsDir,
    include: ['**/*.test.{js,jsx}'],
    // Pure modules run in node; a component test opts into jsdom with a
    // `// @vitest-environment jsdom` docblock, and setup.js then leaves the
    // real DOM alone.
    environment: 'node',
    globals: true,
    setupFiles: [path.join(testsDir, 'setup.js')],
    restoreMocks: true,
  },
  // vite.config.js gets this from @vitejs/plugin-react; here the .jsx files
  // (the app's and a component test's) go through esbuild directly.
  esbuild: { jsx: 'automatic' },
  resolve: { alias: Object.fromEntries(TEST_DEPS.map((d) => [d, nm(d)])) },
  server: { fs: { allow: [path.resolve(here, '..')] } },
});
