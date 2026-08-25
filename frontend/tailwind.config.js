/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Every colour resolves through a CSS variable defined in globals.css,
        // so light and dark are one token set with two value sets rather than
        // two parallel palettes that drift apart.
        bg: 'hsl(var(--bg) / <alpha-value>)',
        surface: 'hsl(var(--surface) / <alpha-value>)',
        'surface-raised': 'hsl(var(--surface-raised) / <alpha-value>)',
        line: 'hsl(var(--line) / <alpha-value>)',
        'line-strong': 'hsl(var(--line-strong) / <alpha-value>)',
        ink: 'hsl(var(--ink) / <alpha-value>)',
        'ink-muted': 'hsl(var(--ink-muted) / <alpha-value>)',
        'ink-subtle': 'hsl(var(--ink-subtle) / <alpha-value>)',
        accent: 'hsl(var(--accent) / <alpha-value>)',
        'accent-soft': 'hsl(var(--accent-soft) / <alpha-value>)',
        'accent-ink': 'hsl(var(--accent-ink) / <alpha-value>)',

        // Section 13 fixes these four across every page:
        // amber = pending, green = recovered, red = unrecoverable, gray = stopped.
        recovered: 'hsl(var(--recovered) / <alpha-value>)',
        pending: 'hsl(var(--pending) / <alpha-value>)',
        unrecoverable: 'hsl(var(--unrecoverable) / <alpha-value>)',
        stopped: 'hsl(var(--stopped) / <alpha-value>)',
      },
      borderRadius: {
        card: '0.875rem',
      },
      fontSize: {
        // Tight, deliberate scale — a dashboard with eleven font sizes reads as
        // noise.
        micro: ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.04em' }],
        metric: ['1.875rem', { lineHeight: '2.25rem', letterSpacing: '-0.02em' }],
        'metric-lg': ['2.25rem', { lineHeight: '2.5rem', letterSpacing: '-0.025em' }],
      },
      boxShadow: {
        card: '0 1px 2px 0 hsl(var(--shadow) / 0.04), 0 1px 3px 0 hsl(var(--shadow) / 0.06)',
        'card-hover':
          '0 2px 4px -1px hsl(var(--shadow) / 0.06), 0 8px 20px -6px hsl(var(--shadow) / 0.12)',
      },
      keyframes: {
        'fade-up': {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        'fade-up': 'fade-up 0.45s cubic-bezier(0.16, 1, 0.3, 1) both',
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [],
};
