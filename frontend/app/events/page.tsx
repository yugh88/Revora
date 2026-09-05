'use client';

import * as React from 'react';
import { useSearchParams } from 'next/navigation';
import { AlertCircle, Download, Inbox, Loader2, RotateCcw, Search, X } from 'lucide-react';

import { EventTable } from '../../components/EventTable';
import { STATUS_LABEL } from '../../components/StatusBadge';
import { Button } from '../../components/ui/button';
import { Card } from '../../components/ui/card';
import { ReportDialog } from '../../components/ReportDialog';
import { AppShell } from '../../components/ui/site-header';
import { LiveIndicator } from '../../components/ui/live-status';
import { useLiveRefresh } from '../../components/ui/use-live-data';
import { cn } from '../../components/ui/utils';
import { api, ApiError, formatCount } from '../../lib/api-client';
import { eventTypeLabel } from '../../lib/labels';
import {
  EVENT_TYPES,
  type EventListQuery,
  type EventListResponse,
  type EventStatus,
  type EventType,
} from '../../lib/types';

/**
 * The revenue-risk event feed. BUILD_SPEC Section 13, page 2.
 *
 * Every filter here maps to a real query parameter on GET /events and is
 * applied SERVER-SIDE. Nothing is filtered again in the browser: one definition
 * of "status=stopped", and pagination counts that actually mean something.
 *
 * The page loads on mount because GET /events is read-only — unlike the
 * dashboard, which needs an explicit action because POST /batch mutates.
 */

const PAGE_SIZE = 50;

const STATUSES: EventStatus[] = [
  'open',
  'diagnosing',
  'intervening',
  'recovered',
  'escalated',
  'unrecoverable',
  'stopped',
];

type ReviewFilter = 'all' | 'review' | 'clean';

export default function EventsPage() {
  return (
    <React.Suspense fallback={null}>
      <EventsFeed />
    </React.Suspense>
  );
}

function EventsFeed() {
  const [data, setData] = React.useState<EventListResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<ApiError | null>(null);

  // The sidebar deep-links here with ?type=..., and the API applies that filter
  // server-side. Reading it from the URL is what makes those six sub-items real
  // navigation rather than labels that all land on the same unfiltered list.
  const searchParams = useSearchParams();
  const urlType = searchParams.get('type') as EventType | null;

  const [status, setStatus] = React.useState<EventStatus | ''>('');
  const [type, setType] = React.useState<EventType | ''>(urlType ?? '');
  const [review, setReview] = React.useState<ReviewFilter>('all');
  const [search, setSearch] = React.useState('');
  const [debounced, setDebounced] = React.useState('');
  const [offset, setOffset] = React.useState(0);

  // Debounced so typing an event id does not fire a request per keystroke.
  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  // Follow the URL when the sidebar changes it, so clicking Payments then
  // Subscriptions actually moves the feed rather than leaving stale state.
  React.useEffect(() => {
    setType(urlType ?? '');
  }, [urlType]);

  // Any filter change resets pagination — staying on page 4 of a result set
  // that now has one page would show an empty table for no reason.
  React.useEffect(() => {
    setOffset(0);
  }, [status, type, review, debounced]);

  const load = React.useCallback(
    async (quiet = false) => {
      // A background refresh never shows a spinner and never clears the table.
      if (!quiet) {
        setLoading(true);
        setError(null);
      }
      const query: EventListQuery = { limit: PAGE_SIZE, offset };
      if (status) query.status = status;
      if (type) query.type = type;
      if (review !== 'all') query.needs_review = review === 'review';
      if (debounced) query.q = debounced;

      try {
        setData(await api.listEvents(query));
        setError(null);
        return true;
      } catch (caught) {
        // Keeps the previous rows: stale data beats an empty table.
        if (!quiet) {
          setError(
            caught instanceof ApiError ? caught : new ApiError('Could not load events.'),
          );
        }
        return false;
      } finally {
        setLoading(false);
      }
    },
    [status, type, review, debounced, offset],
  );

  // Revora works on its own; the shared hook keeps this page in step on the one
  // application-wide interval. Filter changes re-key it, so a new filter
  // refetches immediately rather than waiting for the next tick.
  const [reportOpen, setReportOpen] = React.useState(false);

  const { status: liveStatus, lastUpdated } = useLiveRefresh(load, [
    status,
    type,
    review,
    debounced,
    offset,
  ]);

  const hasFilters = Boolean(status || type || debounced) || review !== 'all';
  const clearFilters = () => {
    setStatus('');
    setType('');
    setReview('all');
    setSearch('');
  };

  const total = data?.total ?? 0;
  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + (data?.returned ?? 0), total);

  return (
    <AppShell>
      <ReportDialog
        kind="recovery"
        title="Recovery cases and the judgement behind them"
        open={reportOpen}
        onClose={() => setReportOpen(false)}
      />

      <main className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-2xl font-semibold tracking-tight text-ink">
                {type ? eventTypeLabel(type) : 'All recoveries'}
              </h1>
              <LiveIndicator status={liveStatus} lastUpdated={lastUpdated} />
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setReportOpen(true)}
              >
                <Download className="h-3.5 w-3.5" aria-hidden="true" />
                Download report
              </Button>
            </div>
            <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
              Every case Revora is working — what happened, why, and what it decided to
              do about it.
            </p>
          </div>
          {data && data.needs_review_count > 0 ? (
            <button
              type="button"
              onClick={() => setReview(review === 'review' ? 'all' : 'review')}
              className={cn(
                'shrink-0 rounded-lg border px-3 py-2 text-left transition-colors',
                'outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg',
                review === 'review'
                  ? 'border-pending/40 bg-pending/10'
                  : 'border-line bg-surface hover:border-line-strong',
              )}
              aria-pressed={review === 'review'}
            >
              <span className="tabular block text-lg font-semibold text-pending">
                {formatCount(data.needs_review_count)}
              </span>
              <span className="text-micro uppercase text-ink-subtle">
                need review
              </span>
            </button>
          ) : null}
        </div>

        {/* ---------------- Filters ---------------- */}
        <Card className="animate-fade-up stagger-1 mt-6 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[200px] flex-1">
              <Search
                className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-subtle"
                aria-hidden="true"
              />
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search by customer…"
                aria-label="Search recoveries by customer"
                className="h-9 w-full rounded-lg border border-line bg-surface pl-8 pr-3 text-xs text-ink outline-none transition-colors placeholder:text-ink-subtle focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
              />
            </div>

            <FilterSelect
              label="Status"
              value={status}
              onChange={(value) => setStatus(value as EventStatus | '')}
              options={[
                { value: '', label: 'All status' },
                ...STATUSES.map((s) => ({ value: s, label: STATUS_LABEL[s] })),
              ]}
            />

            <FilterSelect
              label="Issue"
              value={type}
              onChange={(value) => setType(value as EventType | '')}
              options={[
                { value: '', label: 'All issues' },
                ...EVENT_TYPES.map((t) => ({ value: t, label: eventTypeLabel(t) })),
              ]}
            />

            <FilterSelect
              label="Review"
              value={review}
              onChange={(value) => setReview(value as ReviewFilter)}
              options={[
                { value: 'all', label: 'All cases' },
                { value: 'review', label: 'Needs review' },
                { value: 'clean', label: 'No review needed' },
              ]}
            />

            {hasFilters ? (
              <Button variant="ghost" size="sm" onClick={clearFilters}>
                <X className="h-3.5 w-3.5" aria-hidden="true" />
                Clear
              </Button>
            ) : null}

            {loading && data ? (
              <Loader2
                className="h-4 w-4 animate-spin text-ink-subtle"
                aria-label="Refreshing"
              />
            ) : null}
          </div>
        </Card>

        {/* ---------------- Body ---------------- */}
        <div className="animate-fade-up stagger-2 mt-5">
          {error ? (
            <ErrorState error={error} onRetry={() => void load()} busy={loading} />
          ) : loading && !data ? (
            <TableSkeleton />
          ) : !data || data.items.length === 0 ? (
            <EmptyState hasFilters={hasFilters} onClear={clearFilters} />
          ) : (
            <Card className="overflow-hidden px-2 py-1 lg:px-0 lg:py-0">
              <EventTable events={data.items} from={type ? `type:${type}` : 'events'} />
            </Card>
          )}
        </div>

        {/* ---------------- Pagination ---------------- */}
        {data && data.items.length > 0 ? (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <p className="tabular text-xs text-ink-subtle">
              Showing {formatCount(pageStart)}–{formatCount(pageEnd)} of{' '}
              {formatCount(total)}
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={pageEnd >= total || loading}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        ) : null}
      </main>
    </AppShell>
  );
}

/* -------------------------------------------------------------------------- */

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
}) {
  // A native <select>: it is fully keyboard accessible, works with screen
  // readers, and on mobile opens the platform picker. A custom dropdown would
  // be more work and worse.
  return (
    <label className="relative">
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 cursor-pointer appearance-none rounded-lg border border-line bg-surface pl-3 pr-8 text-xs text-ink outline-none transition-colors hover:border-line-strong focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <span
        aria-hidden="true"
        className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[9px] text-ink-subtle"
      >
        ▼
      </span>
    </label>
  );
}

function TableSkeleton() {
  return (
    <Card className="p-4" role="status" aria-busy="true">
      <span className="sr-only">Loading events</span>
      <div className="space-y-3">
        {Array.from({ length: 8 }).map((_, index) => (
          <div key={index} className="flex items-center gap-4">
            <div className="h-3 w-40 animate-pulse rounded bg-line/60" />
            <div className="h-3 w-28 animate-pulse rounded bg-line/60" />
            <div className="ml-auto h-3 w-24 animate-pulse rounded bg-line/60" />
            <div className="h-3 w-20 animate-pulse rounded bg-line/60" />
          </div>
        ))}
      </div>
    </Card>
  );
}

function EmptyState({
  hasFilters,
  onClear,
}: {
  hasFilters: boolean;
  onClear: () => void;
}) {
  return (
    <Card className="flex flex-col items-center px-6 py-16 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-line bg-surface-raised">
        <Inbox className="h-5 w-5 text-ink-subtle" aria-hidden="true" />
      </span>
      <h2 className="mt-4 text-base font-semibold text-ink">
        {hasFilters ? 'No recoveries match these filters' : 'No recoveries yet'}
      </h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-ink-muted">
        {hasFilters
          ? 'Nothing matches the current combination. Try widening it.'
          : 'Run a recovery analysis and the revenue Revora finds at risk will appear here.'}
      </p>
      <div className="mt-5">
        {hasFilters ? (
          <Button variant="secondary" onClick={onClear}>
            <X className="h-4 w-4" aria-hidden="true" />
            Clear filters
          </Button>
        ) : (
          <Button asChild>
            <a href="/batch">Run a recovery analysis</a>
          </Button>
        )}
      </div>
    </Card>
  );
}

function ErrorState({
  error,
  onRetry,
  busy,
}: {
  error: ApiError;
  onRetry: () => void;
  busy: boolean;
}) {
  return (
    <Card className="border-unrecoverable/25">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-start">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-unrecoverable/10 ring-1 ring-unrecoverable/20">
          <AlertCircle className="h-5 w-5 text-unrecoverable" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink">
            Your recoveries could not be loaded
          </h2>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
            Revora did not change anything. This was a problem reading your recoveries,
            not working them.
          </p>
          <Button
            variant="secondary"
            size="sm"
            className="mt-4"
            onClick={onRetry}
            disabled={busy}
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            Try again
          </Button>
        </div>
      </div>
    </Card>
  );
}
