import '@testing-library/jest-dom/vitest';

// jsdom has no navigation, so the anchor click that saves a file logs a
// "Not implemented" error. It is noise from the environment, not the code —
// and left in place it hides real failures. Silenced narrowly.
const consoleError = console.error;
console.error = (...args: unknown[]) => {
  const first = String(args[0] ?? '');
  if (first.includes('Not implemented: navigation')) return;
  consoleError(...args);
};
