'use client';

import * as React from 'react';
import {
  CreditCard,
  FileText,
  Landmark,
  RefreshCw,
  ShoppingCart,
  Layers,
  type LucideIcon,
} from 'lucide-react';

import { formatCount } from '../lib/api-client';
import {
  EVENT_TYPES,
  EVENT_TYPE_HINTS,
  EVENT_TYPE_LABELS,
  type BatchResponse,
  type EventType,
} from '../lib/types';
import { Card, CardDescription, CardHeader, CardTitle } from './ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { cn } from './ui/utils';

/**
 * Volume across the five recovery directions.
 *
 * The five are fixed by BUILD_SPEC Section 4 and no sixth may ever appear here:
 * the list is driven by EVENT_TYPES, and a key the backend sends that is not one
 * of them is ignored rather than rendered, so a typo upstream cannot invent a
 * direction on screen.
 *
 * Directions with zero events are still listed, greyed. Hiding them would make
 * "no invoices in this batch" indistinguishable from "invoices are not a thing
 * this product does", and the second is false.
 */

const DIRECTION_ICON: Record<EventType, LucideIcon> = {
  payment_degraded: CreditCard,
  checkout_abandoned: ShoppingCart,
  subscription_failed: RefreshCw,
  invoice_overdue: FileText,
  mandate_failed: Landmark,
};

interface Row {
  type: EventType;
  label: string;
  hint: string;
  count: number;
  share: number;
  icon: LucideIcon;
}

function EmptyState() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 py-10 text-center">
      <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-line bg-surface-raised">
        <Layers className="h-5 w-5 text-ink-subtle" aria-hidden="true" />
      </span>
      <p className="text-sm font-medium text-ink">Nothing to break down yet</p>
      <p className="max-w-xs text-xs leading-relaxed text-ink-subtle">
        Revora recovers across five directions. Run an analysis to see how this
        batch distributed across them.
      </p>
    </div>
  );
}

export function DirectionBreakdown({ result }: { result: BatchResponse | null }) {
  const rows: Row[] = React.useMemo(() => {
    const breakdown = result?.event_type_breakdown ?? {};
    const total = EVENT_TYPES.reduce((sum, type) => sum + (breakdown[type] ?? 0), 0);

    return EVENT_TYPES.map((type) => {
      const count = breakdown[type] ?? 0;
      return {
        type,
        label: EVENT_TYPE_LABELS[type],
        hint: EVENT_TYPE_HINTS[type],
        count,
        share: total > 0 ? count / total : 0,
        icon: DIRECTION_ICON[type],
      };
    }).sort((a, b) => b.count - a.count);
  }, [result]);

  const total = rows.reduce((sum, row) => sum + row.count, 0);

  return (
    <Card className="flex h-full flex-col">
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>Recovery directions</CardTitle>
            <CardDescription>Events processed across the five core types.</CardDescription>
          </div>
          {total > 0 ? (
            <span className="tabular shrink-0 text-micro uppercase text-ink-subtle">
              {formatCount(total)} events
            </span>
          ) : null}
        </div>
      </CardHeader>

      {total === 0 ? (
        <EmptyState />
      ) : (
        <div className="flex-1 space-y-4 px-5 pb-5">
          {rows.map((row) => {
            const Icon = row.icon;
            const isEmpty = row.count === 0;

            return (
              <Tooltip key={row.type}>
                <TooltipTrigger asChild>
                  <div
                    tabIndex={0}
                    className={cn(
                      'group -mx-2 rounded-lg px-2 py-1.5 outline-none transition-colors',
                      'hover:bg-surface-raised focus-visible:bg-surface-raised',
                    )}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-2.5">
                        <Icon
                          className={cn(
                            'h-3.5 w-3.5 shrink-0 transition-colors',
                            isEmpty
                              ? 'text-ink-subtle/50'
                              : 'text-ink-subtle group-hover:text-accent',
                          )}
                          aria-hidden="true"
                        />
                        <span
                          className={cn(
                            'truncate text-xs font-medium',
                            isEmpty ? 'text-ink-subtle' : 'text-ink',
                          )}
                        >
                          {row.label}
                        </span>
                      </div>
                      <div className="tabular flex shrink-0 items-baseline gap-2">
                        <span
                          className={cn(
                            'text-xs font-semibold',
                            isEmpty ? 'text-ink-subtle' : 'text-ink',
                          )}
                        >
                          {formatCount(row.count)}
                        </span>
                        <span className="w-10 text-right text-micro text-ink-subtle">
                          {(row.share * 100).toFixed(1)}%
                        </span>
                      </div>
                    </div>

                    <div
                      className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-line/70"
                      role="img"
                      aria-label={`${row.label}: ${row.count} events, ${(row.share * 100).toFixed(1)} percent of this batch`}
                    >
                      <div
                        className={cn(
                          'h-full rounded-full transition-all duration-500 ease-out',
                          isEmpty ? 'bg-transparent' : 'bg-accent group-hover:brightness-110',
                        )}
                        // eslint-disable-next-line react/forbid-dom-props
                        {...{ style: { width: `${Math.max(row.share * 100, isEmpty ? 0 : 1.5)}%` } }}
                      />
                    </div>
                  </div>
                </TooltipTrigger>
                <TooltipContent side="left">
                  <p className="font-medium text-ink">{row.label}</p>
                  <p className="mt-0.5 text-ink-muted">{row.hint}</p>
                  <p className="tabular mt-1.5 text-ink-subtle">
                    {formatCount(row.count)} of {formatCount(total)} events in this batch
                  </p>
                </TooltipContent>
              </Tooltip>
            );
          })}
        </div>
      )}
    </Card>
  );
}
