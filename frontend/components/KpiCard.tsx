'use client';

import * as React from 'react';
import { ArrowDownRight, ArrowUpRight, Minus, type LucideIcon } from 'lucide-react';

import { Card } from './ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { cn } from './ui/utils';

export type KpiTone = 'neutral' | 'accent' | 'recovered' | 'pending' | 'unrecoverable';

/**
 * A measured change between two real runs.
 *
 * `direction` is what the number did; `isGood` is whether that is desirable,
 * which is NOT the same thing — amount at risk going down is good, amount
 * recovered going down is not. Colour is driven by `isGood` so green never
 * means "went up" by accident.
 */
export interface KpiTrend {
  label: string;
  percent: number;
  direction: 'up' | 'down' | 'flat';
  isGood: boolean;
}

export interface KpiCardProps {
  label: string;
  value: string;
  /** Full-precision value shown on hover, e.g. exact paise. */
  exactValue?: string;
  context: string;
  icon: LucideIcon;
  tone?: KpiTone;
  /**
   * Only ever passed when a previous run genuinely exists. There is no
   * placeholder trend — a dashboard that invents "+12%" to look busy is lying.
   */
  trend?: KpiTrend | null;
  /** What this number actually means, for the info tooltip. */
  help?: string;
  className?: string;
}

const TONE_ICON: Record<KpiTone, string> = {
  neutral: 'bg-surface-raised text-ink-muted ring-1 ring-line',
  accent: 'bg-accent/10 text-accent ring-1 ring-accent/20',
  recovered: 'bg-recovered/10 text-recovered ring-1 ring-recovered/20',
  pending: 'bg-pending/10 text-pending ring-1 ring-pending/20',
  unrecoverable: 'bg-unrecoverable/10 text-unrecoverable ring-1 ring-unrecoverable/20',
};

const TONE_RAIL: Record<KpiTone, string> = {
  neutral: 'group-hover:bg-line-strong',
  accent: 'group-hover:bg-accent',
  recovered: 'group-hover:bg-recovered',
  pending: 'group-hover:bg-pending',
  unrecoverable: 'group-hover:bg-unrecoverable',
};

export function KpiCard({
  label,
  value,
  exactValue,
  context,
  icon: Icon,
  tone = 'neutral',
  trend,
  help,
  className,
}: KpiCardProps) {
  const TrendIcon =
    trend?.direction === 'up'
      ? ArrowUpRight
      : trend?.direction === 'down'
        ? ArrowDownRight
        : Minus;

  return (
    <Card
      // tabIndex makes the card reachable by keyboard so its hover affordance
      // is not mouse-only. focus-within keeps the treatment while the inner
      // tooltip trigger holds focus.
      tabIndex={0}
      className={cn(
        'group relative isolate overflow-hidden p-5 outline-none transition-all duration-200',
        'hover:-translate-y-0.5 hover:border-line-strong hover:shadow-card-hover',
        'focus-visible:-translate-y-0.5 focus-visible:border-line-strong focus-visible:shadow-card-hover',
        className,
      )}
    >
      {/* Left rail: the only colour that moves on hover. Restrained on purpose. */}
      <span
        aria-hidden="true"
        className={cn(
          'absolute inset-y-0 left-0 w-[2px] bg-transparent transition-colors duration-200',
          TONE_RAIL[tone],
        )}
      />

      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-1.5">
          <p className="text-micro font-semibold uppercase text-ink-subtle">{label}</p>
          {help ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label={`What ${label} means`}
                  className="flex h-4 w-4 items-center justify-center rounded-full border border-line text-[9px] font-semibold text-ink-subtle transition-colors hover:border-line-strong hover:text-ink-muted"
                >
                  i
                </button>
              </TooltipTrigger>
              <TooltipContent>{help}</TooltipContent>
            </Tooltip>
          ) : null}
        </div>

        <span
          className={cn(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-transform duration-200 group-hover:scale-105',
            TONE_ICON[tone],
          )}
        >
          <Icon className="h-4 w-4" aria-hidden="true" />
        </span>
      </div>

      <p
        className="tabular mt-4 text-metric font-semibold text-ink"
        title={exactValue ?? undefined}
      >
        {value}
      </p>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1">
        {trend ? (
          <span
            className={cn(
              'tabular inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-xs font-medium',
              trend.direction === 'flat'
                ? 'bg-surface-raised text-ink-subtle'
                : trend.isGood
                  ? 'bg-recovered/10 text-recovered'
                  : 'bg-unrecoverable/10 text-unrecoverable',
            )}
          >
            <TrendIcon className="h-3 w-3" aria-hidden="true" />
            {trend.direction === 'flat' ? 'no change' : `${Math.abs(trend.percent).toFixed(1)}%`}
            <span className="sr-only">
              {trend.direction === 'flat'
                ? ' compared with the previous run'
                : ` ${trend.direction} compared with the previous run`}
            </span>
          </span>
        ) : null}
        <p className="text-xs leading-relaxed text-ink-subtle">
          {trend ? trend.label : context}
        </p>
      </div>
    </Card>
  );
}
