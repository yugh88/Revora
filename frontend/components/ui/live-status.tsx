'use client';

import * as React from 'react';

import type { LiveStatus } from '../../lib/api-client';
import { cn } from './utils';

/**
 * "● Live · Updated just now".
 *
 * Deliberately says nothing technical. A merchant does not need to know the
 * polling interval, the API's health or which gateway is configured — only
 * whether what they are looking at is current. When it is not, the word is
 * "Reconnecting", not an error code.
 *
 * The relative time re-renders on its own so "just now" does not sit there
 * being wrong a minute later.
 */
export function LiveIndicator({
  status,
  lastUpdated,
}: {
  status: LiveStatus;
  lastUpdated: Date | null;
}) {
  const [, setTick] = React.useState(0);

  React.useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), 10_000);
    return () => clearInterval(timer);
  }, []);

  const label = React.useMemo(() => {
    if (status === 'reconnecting') return 'Reconnecting…';
    if (!lastUpdated) return 'Updating…';
    const seconds = Math.round((Date.now() - lastUpdated.getTime()) / 1000);
    if (seconds < 10) return 'Updated just now';
    if (seconds < 60) return `Updated ${seconds}s ago`;
    const minutes = Math.round(seconds / 60);
    return `Updated ${minutes} min ago`;
  }, [status, lastUpdated]);

  return (
    <span
      className="inline-flex items-center gap-1.5 text-micro text-ink-subtle"
      aria-live="polite"
    >
      <span
        aria-hidden="true"
        className={cn(
          'h-1.5 w-1.5 rounded-full',
          status === 'live' ? 'bg-recovered' : 'animate-pulse bg-pending',
        )}
      />
      {status === 'live' ? 'Live' : 'Reconnecting'}
      <span className="text-ink-subtle/70">· {label}</span>
    </span>
  );
}
