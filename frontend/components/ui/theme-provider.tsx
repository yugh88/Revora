'use client';

import * as React from 'react';
import { ThemeProvider as NextThemesProvider } from 'next-themes';

/**
 * Theme provider.
 *
 * A thin client boundary so app/layout.tsx can stay a server component and keep
 * its metadata export. next-themes writes the class to <html> before paint via
 * the script layout.tsx enables with suppressHydrationWarning, which is what
 * prevents both a flash of the wrong theme and a hydration mismatch.
 */
export function ThemeProvider({
  children,
  ...props
}: React.ComponentProps<typeof NextThemesProvider>) {
  return <NextThemesProvider {...props}>{children}</NextThemesProvider>;
}
