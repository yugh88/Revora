'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { AlertTriangle, ChevronRight } from 'lucide-react';

import { formatDateTime, formatInr, formatInrExact, formatRelative } from '../lib/api-client';
import { actionLabel, eventTypeLabel, reviewReasonLabel, rootCauseLabel } from '../lib/labels';
import type { EventSummary } from '../lib/types';
import { StatusBadge, StatusDot } from './StatusBadge';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { cn } from './ui/utils';

/**
 * The risk-event feed.
 *
 * A real <table> on desktop, because this is tabular data and screen readers,
 * column headers and keyboard navigation all depend on it being one. Below the
 * lg breakpoint it becomes a stacked card list — a horizontally scrolling
 * eleven-column table on a phone is unusable, and hiding columns silently would
 * hide the amount.
 *
 * Rows are links, not clickable divs, so middle-click, cmd-click and the
 * keyboard all work without reimplementing browser behaviour.
 */

function NeedsReviewFlag({ reasons }: { reasons: string[] }) {
  if (!reasons.length) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-pending/10 text-pending outline-none ring-1 ring-pending/20 focus-visible:ring-2 focus-visible:ring-accent"
          aria-label={`Needs review: ${reasons.map(reviewReasonLabel).join('; ')}`}
        >
          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
        </span>
      </TooltipTrigger>
      <TooltipContent>
        <p className="font-medium text-ink">Needs review</p>
        <ul className="mt-1 space-y-0.5">
          {reasons.map((reason) => (
            <li key={reason} className="text-ink-muted">
              {reviewReasonLabel(reason)}
            </li>
          ))}
        </ul>
      </TooltipContent>
    </Tooltip>
  );
}

function RootCauseCell({ event }: { event: EventSummary }) {
  if (!event.root_cause) {
    return <span className="text-xs text-ink-subtle">—</span>;
  }
  // A confidence percentage is an implementation detail a merchant cannot act
  // on. What they need to know is whether a person should look at it.
  const needsReview = event.confidence !== null && event.confidence < 0.6;
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-ink">{rootCauseLabel(event.root_cause)}</span>
      {needsReview ? (
        <span
          className="shrink-0 rounded bg-pending/10 px-1.5 py-0.5 text-micro font-medium text-pending"
          title="Revora could not determine this confidently, so it did not act automatically"
        >
          Needs review
        </span>
      ) : null}
    </span>
  );
}

/**
 * Column widths are declared rather than left to the browser.
 *
 * Auto-layout gave Customer and Issue whatever space was left after the wide
 * columns took theirs, which is how names ended up clipped and headings
 * collided. Fixed minimums plus horizontal overflow is the honest trade: a
 * name is either readable or it is not, and squeezing it to fit helps nobody.
 */
const COLUMNS: Array<{
  key: string;
  label: string;
  width: string;
  align?: 'right';
}> = [
  { key: 'customer', label: 'Customer', width: 'min-w-[190px]' },
  { key: 'issue', label: 'Issue', width: 'min-w-[170px]' },
  { key: 'amount', label: 'Amount', width: 'min-w-[110px]', align: 'right' },
  { key: 'status', label: 'Status', width: 'min-w-[130px]' },
  { key: 'reason', label: 'Reason', width: 'min-w-[210px]' },
  { key: 'action', label: 'Recovery action', width: 'min-w-[190px]' },
  { key: 'detected', label: 'Detected', width: 'min-w-[120px]' },
  { key: 'chevron', label: '', width: 'w-10' },
];

export function EventTable({
  events,
  from = 'events',
}: {
  events: EventSummary[];
  /** Where a click should return to, so the case knows its origin. */
  from?: string;
}) {
  const router = useRouter();

  return (
    <>
      {/* ---------------- Desktop: real table ---------------- */}
      <div className="hidden overflow-x-auto lg:block">
        <table className="w-full min-w-[1080px] border-collapse text-left">
          <caption className="sr-only">
            Revenue at risk. Each row opens the full recovery story.
          </caption>
          <thead>
            <tr>
              {COLUMNS.map((column) => (
                <th
                  key={column.label || column.key}
                  scope="col"
                  className={cn(
                    // Not sticky. It previously stuck at top-16 to sit under
                    // the app bar, but the table scrolls inside the page rather
                    // than the viewport, so the header floated over row one.
                    // A plain header with real padding is correct and cannot
                    // collide with anything.
                    'whitespace-nowrap border-b border-line bg-surface px-4 py-3 text-micro font-semibold uppercase leading-none text-ink-subtle',
                    column.align === 'right' && 'text-right',
                    column.width,
                  )}
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr
                key={event.id}
                // §15: the WHOLE row opens the case. The anchor inside the first
                // cell stays, so middle-click and cmd-click still work and the
                // row is reachable from the keyboard; this handler just makes
                // the rest of the row behave the way it looks.
                onClick={() => router.push(`/events/${event.id}?from=${from}`)}
                className="group cursor-pointer border-b border-line/70 transition-colors last:border-0 hover:bg-surface-raised/70 focus-within:bg-surface-raised/70"
              >
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <NeedsReviewFlag reasons={event.review_reasons} />
                    <Link
                      href={`/events/${event.id}?from=${from}`}
                      className="rounded outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    >
                      <span className="whitespace-nowrap text-xs font-medium text-ink group-hover:text-accent">
                        {event.customer_name}
                      </span>
                    </Link>
                  </div>
                </td>
                <td className="px-4 py-3">
                  <span className="whitespace-nowrap text-xs text-ink-muted">
                    {eventTypeLabel(event.type)}
                  </span>
                </td>
                <td
                  className="tabular whitespace-nowrap px-4 py-3 text-right text-xs font-semibold text-ink"
                  title={formatInrExact(event.amount)}
                >
                  {formatInr(event.amount)}
                </td>
                <td className="px-4 py-3">
                  <StatusDot status={event.status} />
                </td>
                <td className="px-4 py-3">
                  <RootCauseCell event={event} />
                </td>
                <td className="px-4 py-3">
                  {event.action_code ? (
                    <span className="text-xs text-ink-muted">
                      {actionLabel(event.action_code)}
                    </span>
                  ) : (
                    <span className="text-xs text-ink-subtle">—</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <time
                    dateTime={event.detected_at}
                    title={formatDateTime(event.detected_at)}
                    className="whitespace-nowrap text-xs text-ink-subtle"
                  >
                    {formatRelative(event.detected_at)}
                  </time>
                </td>
                <td className="px-4 py-3 text-right">
                  <ChevronRight
                    className="ml-auto h-4 w-4 text-ink-subtle/50 transition-transform group-hover:translate-x-0.5 group-hover:text-accent"
                    aria-hidden="true"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ---------------- Mobile / tablet: stacked cards ---------------- */}
      <ul className="divide-y divide-line lg:hidden">
        {events.map((event) => (
          <li key={event.id}>
            <Link
              href={`/events/${event.id}?from=${from}`}
              className="block px-1 py-3 outline-none transition-colors hover:bg-surface-raised/70 focus-visible:bg-surface-raised/70"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <NeedsReviewFlag reasons={event.review_reasons} />
                    <span className="truncate text-xs font-medium text-ink">
                      {event.customer_name}
                    </span>
                  </div>
                  <p className="mt-0.5 text-micro text-ink-subtle">
                    {eventTypeLabel(event.type)}
                  </p>
                </div>
                <span
                  className="tabular shrink-0 text-sm font-semibold text-ink"
                  title={formatInrExact(event.amount)}
                >
                  {formatInr(event.amount)}
                </span>
              </div>

              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                <StatusBadge status={event.status} />
                {event.root_cause ? (
                  <span className="text-micro text-ink-muted">
                    {rootCauseLabel(event.root_cause)}
                  </span>
                ) : null}
                <time
                  dateTime={event.detected_at}
                  className="ml-auto text-micro text-ink-subtle"
                >
                  {formatRelative(event.detected_at)}
                </time>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
