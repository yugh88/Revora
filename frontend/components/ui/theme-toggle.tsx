'use client';

import * as React from 'react';
import { Monitor, Moon, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';

import { Button } from './button';
import { Tooltip, TooltipContent, TooltipTrigger } from './tooltip';

const ORDER = ['light', 'dark', 'system'] as const;
type ThemeName = (typeof ORDER)[number];

const META: Record<ThemeName, { icon: typeof Sun; label: string }> = {
  light: { icon: Sun, label: 'Light' },
  dark: { icon: Moon, label: 'Dark' },
  system: { icon: Monitor, label: 'System' },
};

/**
 * Cycles light → dark → system, persisting through next-themes (localStorage).
 *
 * The mounted guard matters. On the server there is no way to know the stored
 * preference, so rendering the resolved icon immediately would produce markup
 * that disagrees with the client and React would log a hydration mismatch. A
 * fixed-size placeholder is rendered until mount, so the layout also does not
 * shift when the real icon appears.
 */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => setMounted(true), []);

  if (!mounted) {
    return (
      <div
        className="h-9 w-9 rounded-lg border border-line bg-surface"
        aria-hidden="true"
      />
    );
  }

  const current = (ORDER as readonly string[]).includes(theme ?? '')
    ? (theme as ThemeName)
    : 'system';
  const next = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];
  const Icon = META[current].icon;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="secondary"
          size="icon"
          onClick={() => setTheme(next)}
          aria-label={`Theme: ${META[current].label}. Switch to ${META[next].label}.`}
        >
          <Icon className="h-4 w-4" aria-hidden="true" />
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        Theme: <span className="font-medium">{META[current].label}</span> — click for{' '}
        {META[next].label.toLowerCase()}
      </TooltipContent>
    </Tooltip>
  );
}
