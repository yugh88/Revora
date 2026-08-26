'use client';

import * as React from 'react';
import Link from 'next/link';
import { AlertTriangle, ChevronRight } from 'lucide-react';

import { formatDateTime, formatInr, formatInrExact, formatRelative, humanizeKey } from '../lib/api-client';
import { EVENT_TYPE_LABELS, type EventSummary } from '../lib/types';
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
          aria-label={`Needs review: ${reasons.join('; ')}`}
        >
          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
        </span>
      </TooltipTrigger>
      <TooltipContent>
        <p className="font-medium text-ink">Needs review</p>
        <ul className="mt-1 space-y-0.5">
          {reasons.map((reason) => (
            <li key={reason} className="text-ink-muted">
              {reason}
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
  const low = event.confidence !== null && event.confidence < 0.6;
  return (
    <span className="flex items-center gap-1.5">
      <span className="truncate text-xs text-ink">{humanizeKey(event.root_cause)}</span>
      {event.confidence !== null ? (
        <span
          className={cn(
            'tabular shrink-0 rounded px-1 py-0.5 text-micro font-medium',
            low ? 'bg-pending/10 text-pending' : 'bg-surface-raised text-ink-subtle',
          )}
          title={low ? 'Below the engine confidence threshold' : undefined}
        >
          {(event.confidence * 100).toFixed(0)}%
        </span>
      ) : null}
    </span>
  );
}

export function EventTable({ events }: { events: EventSummary[] }) {
  return (
    <>
      {/* ---------------- Desktop: real table ---------------- */}
      <div className="hidden overflow-x-auto lg:block">
        <table className="w-full border-collapse text-left">
          <caption className="sr-only">
            Revenue-risk events. Each row links to the full drill-down.
          </caption>
          <thead>
            <tr className="border-b border-line">
              {[
                'Event',
                'Type',
                'Amount',
                'Status',
                'Root cause',
                'Action',
                'Detected',
                '',
              ].map((heading, index) => (
                <th
                  key={heading || index}
                  scope="col"
                  className={cn(
                    'sticky top-16 z-10 bg-bg/95 px-3 py-2.5 text-micro font-semibold uppercase text-ink-subtle backdrop-blur',
                    heading === 'Amount' && 'text-right',
                  )}
                >
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr
                key={event.id}
                className="group border-b border-line/70 transition-colors last:border-0 hover:bg-surface-raised/70 focus-within:bg-surface-raised/70"
              >
                <td className="px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <NeedsReviewFlag reasons={event.review_reasons} />
                    <Link
                      href={`/events/${event.id}`}
                      className="rounded outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    >
                      <code className="text-xs font-medium text-ink group-hover:text-accent">
                        {event.id}
                      </code>
                      <span className="block text-micro text-ink-subtle">
                        {event.customer_id}
                      </span>
                    </Link>
                  </div>
                </td>
                <td className="px-3 py-2.5">
                  <span className="text-xs text-ink-muted">
                    {EVENT_TYPE_LABELS[event.type] ?? humanizeKey(event.type)}
                  </span>
                </td>
                <td
                  className="tabular px-3 py-2.5 text-right text-xs font-semibold text-ink"
                  title={formatInrExact(event.amount)}
                >
                  {formatInr(event.amount)}
                </td>
                <td className="px-3 py-2.5">
                  <StatusDot status={event.status} />
                </td>
                <td className="max-w-[200px] px-3 py-2.5">
                  <RootCauseCell event={event} />
                </td>
                <td className="px-3 py-2.5">
                  {event.action_code ? (
                    <span className="text-xs text-ink-muted">
                      {humanizeKey(event.action_code)}
                    </span>
                  ) : (
                    <span className="text-xs text-ink-subtle">—</span>
                  )}
                </td>
                <td className="px-3 py-2.5">
                  <time
                    dateTime={event.detected_at}
                    title={formatDateTime(event.detected_at)}
                    className="text-xs text-ink-subtle"
                  >
                    {formatRelative(event.detected_at)}
                  </time>
                </td>
                <td className="px-3 py-2.5 text-right">
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
              href={`/events/${event.id}`}
              className="block px-1 py-3 outline-none transition-colors hover:bg-surface-raised/70 focus-visible:bg-surface-raised/70"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <NeedsReviewFlag reasons={event.review_reasons} />
                    <code className="truncate text-xs font-medium text-ink">{event.id}</code>
                  </div>
                  <p className="mt-0.5 text-micro text-ink-subtle">
                    {EVENT_TYPE_LABELS[event.type] ?? humanizeKey(event.type)} ·{' '}
                    {event.customer_id}
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
                    {humanizeKey(event.root_cause)}
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
