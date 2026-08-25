import * as React from 'react';

import { Card } from './ui/card';
import { cn } from './ui/utils';

/**
 * Loading placeholders.
 *
 * Each skeleton matches the FOOTPRINT of the thing it stands in for, so the
 * layout does not jump when real data arrives. A generic spinner would be less
 * work and worse: it tells the user nothing about what is coming.
 *
 * The shimmer is a single translating highlight, paused entirely under
 * prefers-reduced-motion by the global rule in globals.css.
 */

export function Shimmer({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'relative overflow-hidden rounded-md bg-line/60',
        'after:absolute after:inset-0 after:-translate-x-full after:animate-shimmer',
        'after:bg-gradient-to-r after:from-transparent after:via-surface-raised/70 after:to-transparent',
        className,
      )}
    />
  );
}

export function KpiCardSkeleton() {
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <Shimmer className="h-3 w-24" />
        <Shimmer className="h-8 w-8 rounded-lg" />
      </div>
      <Shimmer className="mt-4 h-8 w-32" />
      <Shimmer className="mt-3 h-3 w-40" />
    </Card>
  );
}

export function ChartSkeleton() {
  // Bar heights are fixed, not random: a skeleton that reshuffles on every
  // render reads as flicker.
  const heights = [42, 68, 55, 80, 62, 88, 71];
  return (
    <Card className="flex h-full flex-col p-5">
      <Shimmer className="h-3.5 w-40" />
      <Shimmer className="mt-2 h-3 w-56" />
      <div className="mt-8 flex flex-1 items-end gap-3" aria-hidden="true">
        {heights.map((height, index) => (
          <Shimmer
            key={index}
            className="w-full rounded-t-md"
            // eslint-disable-next-line react/forbid-dom-props
            {...{ style: { height: `${height}%` } }}
          />
        ))}
      </div>
      <Shimmer className="mt-4 h-3 w-full" />
    </Card>
  );
}

export function BreakdownSkeleton() {
  return (
    <Card className="flex h-full flex-col p-5">
      <Shimmer className="h-3.5 w-36" />
      <Shimmer className="mt-2 h-3 w-44" />
      <div className="mt-6 space-y-5">
        {[0, 1, 2, 3, 4].map((index) => (
          <div key={index} className="space-y-2">
            <div className="flex items-center justify-between gap-4">
              <Shimmer className="h-3 w-32" />
              <Shimmer className="h-3 w-12" />
            </div>
            <Shimmer className="h-2 w-full rounded-full" />
          </div>
        ))}
      </div>
    </Card>
  );
}

/** The whole dashboard body while the first analysis is running. */
export function DashboardSkeleton() {
  return (
    <div className="space-y-6" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">Running analysis, please wait.</span>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((index) => (
          <KpiCardSkeleton key={index} />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <div className="h-[380px]">
            <ChartSkeleton />
          </div>
        </div>
        <div className="h-[380px]">
          <BreakdownSkeleton />
        </div>
      </div>
    </div>
  );
}
