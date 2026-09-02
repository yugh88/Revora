import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

/**
 * Test setup for the frontend.
 *
 * There was none before this: no runner, no test files. The refactor that
 * merged Recovery Messages into Communications is exactly the kind of change
 * that needs a regression net, so one was added rather than asserted about.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    include: ['__tests__/**/*.test.{ts,tsx}'],
  },
});
