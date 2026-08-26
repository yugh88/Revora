import * as React from 'react';

import { Badge } from './ui/badge';
import { cn } from './ui/utils';
import type { EventStatus } from '../lib/types';

/**
 * Event status, rendered consistently everywhere.
 *
 * BUILD_SPEC Section 13: "Consistent status colors (amber=pending,
 * green=recovered, red=unrecoverable, gray=stopped) across every page." This
 * component is the single place that mapping lives, so no page can invent its
 * own palette and quietly break the convention.
 *
 * The seven lifecycle statuses map onto those four colours:
 *   open, diagnosing   -> neutral   (detected, not yet acted on)
 *   intervening, escalated -> amber (in flight — the "pending" family)
 *   recovered          -> green
 *   unrecoverable      -> red
 *   stopped            -> grey
 *
 * Each also carries a short explanation, because "stopped" and "unrecoverable"
 * are not self-evident and the difference matters: one is the engine choosing
 * not to act, the other is money that is gone.
 */

type Variant = React.ComponentProps<typeof Badge>['variant'];

const STATUS_VARIANT: Record<EventStatus, Variant> = {
  open: 'neutral',
  diagnosing: 'neutral',
  intervening: 'pending',
  escalated: 'pending',
  recovered: 'recovered',
  unrecoverable: 'unrecoverable',
  stopped: 'stopped',
};

export const STATUS_LABEL: Record<EventStatus, string> = {
  open: 'Open',
  diagnosing: 'Diagnosing',
  intervening: 'Intervening',
  escalated: 'Escalated',
  recovered: 'Recovered',
  unrecoverable: 'Unrecoverable',
  stopped: 'Stopped',
};

export const STATUS_HINT: Record<EventStatus, string> = {
  open: 'Detected, not yet reasoned about.',
  diagnosing: 'Root-cause classification in flight.',
  intervening: 'An action has been executed and the outcome is still open.',
  escalated: 'Handed to a human. The engine takes no further automated action.',
  recovered: 'Money actually collected. Terminal.',
  unrecoverable: 'Definitively lost or exhausted. Terminal.',
  stopped: 'The engine deliberately declined to act further under policy.',
};

/** Dot colour, for dense contexts where a full badge is too heavy. */
const STATUS_DOT: Record<EventStatus, string> = {
  open: 'bg-ink-subtle',
  diagnosing: 'bg-ink-subtle',
  intervening: 'bg-pending',
  escalated: 'bg-pending',
  recovered: 'bg-recovered',
  unrecoverable: 'bg-unrecoverable',
  stopped: 'bg-stopped',
};

export function StatusBadge({
  status,
  className,
}: {
  status: EventStatus;
  className?: string;
}) {
  return (
    <Badge variant={STATUS_VARIANT[status]} className={cn('gap-1.5', className)}>
      <span
        aria-hidden="true"
        className={cn('h-1.5 w-1.5 shrink-0 rounded-full', STATUS_DOT[status])}
      />
      {STATUS_LABEL[status]}
    </Badge>
  );
}

/** Status as a bare dot plus label — for table rows, where badges get noisy. */
export function StatusDot({
  status,
  className,
}: {
  status: EventStatus;
  className?: string;
}) {
  return (
    <span className={cn('inline-flex items-center gap-2', className)}>
      <span
        aria-hidden="true"
        className={cn('h-2 w-2 shrink-0 rounded-full', STATUS_DOT[status])}
      />
      <span className="text-xs font-medium text-ink">{STATUS_LABEL[status]}</span>
    </span>
  );
}
