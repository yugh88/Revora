import type { Metadata, Viewport } from 'next';

import { ThemeProvider } from '../components/ui/theme-provider';
import { TooltipProvider } from '../components/ui/tooltip';
import './globals.css';

export const metadata: Metadata = {
  title: 'Revora — Revenue recovery that reasons',
  description:
    'Revora detects revenue at risk, diagnoses why it failed, decides what to do within policy, and stops when it should. Razorpay Buildathon — Track 03.',
};

export const viewport: Viewport = {
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#fbfbfd' },
    { media: '(prefers-color-scheme: dark)', color: '#0e0f16' },
  ],
};

/**
 * Root layout — a server component.
 *
 * The client boundary is pushed down into ThemeProvider so this file keeps its
 * metadata export and ships no JavaScript of its own.
 *
 * `suppressHydrationWarning` on <html> is required, not a workaround:
 * next-themes writes the resolved theme class onto <html> in a blocking script
 * before first paint, so the server-rendered markup and the pre-hydration DOM
 * genuinely differ by that one attribute. Suppressing it here is the documented
 * approach and is scoped to this element alone.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen bg-bg antialiased">
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          <TooltipProvider delayDuration={200} skipDelayDuration={300}>
            <a
              href="#main"
              className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-accent focus:px-4 focus:py-2 focus:text-sm focus:text-accent-ink"
            >
              Skip to content
            </a>
            {children}
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
