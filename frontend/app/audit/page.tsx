'use client';

import * as React from 'react';
import Link from 'next/link';
import { AlertCircle, ArrowRight, Download, FileClock, Loader2, RotateCcw, X } from 'lucide-react';

import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card } from '../../components/ui/card';
import { ReportDialog } from '../../components/ReportDialog';
import { AppShell } from '../../components/ui/site-header';
import { LiveIndicator } from '../../components/ui/live-status';
import { useLiveRefresh } from '../../components/ui/use-live-data';
import { cn } from '../../components/ui/utils';
import { api, ApiError, formatCount, formatDateTime, formatRelative } from '../../lib/api-client';
import { auditActionLabel, humanSentence, stageLabel } from '../../lib/labels';
import { PIPELINE_STAGES, type AuditListResponse, type AuditQuery } from '../../lib/types';

/**
 * The immutable audit log. BUILD_SPEC Sections 4, 10 and 13.
 *
 * Read-only over real persisted rows. Opening this page writes nothing — an
 * audit trail that grew because somebody looked at it would be worthless.
 *
 * Filtering is server-side on GET /audit; the stage counts describe the whole
 * filtered set rather than the visible page, so the numbers mean something.
 */

const PAGE_SIZE = 50;

const STAGE_TONE: Record<string, string> = {
  detection: 'bg-ink-subtle',
  diagnosis: 'bg-accent',
  decision: 'bg-accent',
  policy: 'bg-stopped',
  execution: 'bg-pending',
  verification: 'bg-pending',
  recovery: 'bg-recovered',
  escalation: 'bg-pending',
};

export default function AuditPage() {
  const [data, setData] = React.useState<AuditListResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<ApiError | null>(null);
  const [stage, setStage] = React.useState('');
  const [search, setSearch] = React.useState('');
  const [debounced, setDebounced] = React.useState('');
  const [offset, setOffset] = React.useState(0);

  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  React.useEffect(() => {
    setOffset(0);
  }, [stage, debounced]);

  const load = React.useCallback(
    async (quiet = false) => {
      if (!quiet) {
        setLoading(true);
        setError(null);
      }
      const query: AuditQuery = { limit: PAGE_SIZE, offset, order: 'desc' };
      if (stage) query.stage = stage;
      if (debounced) query.event_id = debounced;
      try {
        setData(await api.listAudit(query));
        setError(null);
        return true;
      } catch (caught) {
        if (!quiet) {
          setError(
            caught instanceof ApiError
              ? caught
              : new ApiError('Could not load the audit log.'),
          );
        }
        return false;
      } finally {
        setLoading(false);
      }
    },
    [stage, debounced, offset],
  );

  // New audit records appear as Revora works, without anyone reloading.
  const [reportOpen, setReportOpen] = React.useState(false);

  const { status: liveStatus, lastUpdated } = useLiveRefresh(load, [
    stage,
    debounced,
    offset,
  ]);

  const total = data?.total ?? 0;
  const pageEnd = Math.min(offset + (data?.returned ?? 0), total);

  return (
    <AppShell>
      <ReportDialog
        kind="audit"
        title="Every step Revora took, for a period you choose"
        open={reportOpen}
        onClose={() => setReportOpen(false)}
      />
      <main className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Audit</h1>
            <LiveIndicator status={liveStatus} lastUpdated={lastUpdated} />
            <Button
              variant="secondary"
              size="sm"
              className="ml-auto"
              onClick={() => setReportOpen(true)}
            >
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              Download report
            </Button>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
            Every step Revora took on your behalf, in order and never edited — so any
            recovery can be explained after the fact.
          </p>
        </div>

        <Card className="animate-fade-up stagger-1 mt-6 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Filter by customer or case…"
              aria-label="Filter audit records by customer or case"
              className="h-9 min-w-[220px] flex-1 rounded-lg border border-line bg-surface px-3 text-xs text-ink outline-none transition-colors placeholder:text-ink-subtle focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
            />
            <label className="relative">
              <span className="sr-only">Stage</span>
              <select
                value={stage}
                onChange={(event) => setStage(event.target.value)}
                className="h-9 cursor-pointer appearance-none rounded-lg border border-line bg-surface pl-3 pr-8 text-xs text-ink outline-none hover:border-line-strong focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
              >
                <option value="">All stages</option>
                {PIPELINE_STAGES.map((value) => (
                  <option key={value} value={value}>
                    {stageLabel(value)}
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
            {stage || debounced ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setStage('');
                  setSearch('');
                }}
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
                Clear
              </Button>
            ) : null}
            {loading && data ? (
              <Loader2 className="h-4 w-4 animate-spin text-ink-subtle" aria-label="Refreshing" />
            ) : null}
          </div>

          {data && Object.keys(data.stage_breakdown).length > 0 ? (
            <div className="mt-3 flex flex-wrap gap-1.5 border-t border-line pt-3">
              {PIPELINE_STAGES.filter((value) => data.stage_breakdown[value]).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setStage(stage === value ? '' : value)}
                  aria-pressed={stage === value}
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-micro transition-colors',
                    'outline-none focus-visible:ring-2 focus-visible:ring-accent',
                    stage === value
                      ? 'border-accent/40 bg-accent/10 text-accent'
                      : 'border-line text-ink-muted hover:border-line-strong',
                  )}
                >
                  <span
                    aria-hidden="true"
                    className={cn('h-1.5 w-1.5 rounded-full', STAGE_TONE[value] ?? 'bg-ink-subtle')}
                  />
                  {stageLabel(value)}
                  <span className="tabular font-semibold">{data.stage_breakdown[value]}</span>
                </button>
              ))}
            </div>
          ) : null}
        </Card>

        <div className="animate-fade-up stagger-2 mt-5">
          {error ? (
            <ErrorState error={error} onRetry={() => void load()} busy={loading} />
          ) : loading && !data ? (
            <AuditSkeleton />
          ) : !data || data.items.length === 0 ? (
            <EmptyState hasFilters={Boolean(stage || debounced)} />
          ) : (
            <Card className="overflow-hidden">
              <ul className="divide-y divide-line">
                {data.items.map((entry) => {
                  // The WHOLE row is the target, not just the id. A real <Link>
                  // rather than an onClick div, so middle-click, cmd-click and
                  // the keyboard all behave the way a link should.
                  const body = (
                    <>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                        <span
                          aria-hidden="true"
                          className={cn(
                            'h-2 w-2 shrink-0 rounded-full',
                            STAGE_TONE[entry.stage] ?? 'bg-ink-subtle',
                          )}
                        />
                        <Badge variant="neutral">{stageLabel(entry.stage)}</Badge>
                        <span className="text-xs font-medium text-ink">
                          {auditActionLabel(entry.action)}
                        </span>
                        <time
                          dateTime={entry.timestamp}
                          title={formatDateTime(entry.timestamp)}
                          className="tabular ml-auto text-micro text-ink-subtle"
                        >
                          {formatRelative(entry.timestamp)}
                        </time>
                      </div>

                      {entry.reasoning ? (
                        <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
                          {humanSentence(entry.reasoning)}
                        </p>
                      ) : null}

                      {entry.event_id ? (
                        <p className="tabular mt-1.5 flex items-center gap-1 text-micro text-ink-subtle">
                          <span>{entry.event_id}</span>
                          <ArrowRight
                            className="h-3 w-3 transition-transform group-hover:translate-x-0.5"
                            aria-hidden="true"
                          />
                        </p>
                      ) : null}
                    </>
                  );

                  return (
                    <li key={entry.id}>
                      {entry.event_id ? (
                        <Link
                          href={`/events/${entry.event_id}?from=audit`}
                          className="group block px-4 py-3 outline-none transition-colors hover:bg-surface-raised/70 focus-visible:bg-surface-raised/70 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
                        >
                          {body}
                        </Link>
                      ) : (
                        <div className="px-4 py-3">{body}</div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}
        </div>

        {data && data.items.length > 0 ? (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <p className="tabular text-xs text-ink-subtle">
              Showing {formatCount(offset + 1)}–{formatCount(pageEnd)} of {formatCount(total)}
            </p>
            <div className="flex gap-2">
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

function AuditSkeleton() {
  return (
    <Card className="p-4" role="status" aria-busy="true">
      <span className="sr-only">Loading audit entries</span>
      <div className="space-y-4">
        {Array.from({ length: 10 }).map((_, index) => (
          <div key={index} className="space-y-1.5">
            <div className="flex items-center gap-3">
              <div className="h-4 w-20 animate-pulse rounded-full bg-line/60" />
              <div className="h-3 w-40 animate-pulse rounded bg-line/60" />
              <div className="ml-auto h-3 w-16 animate-pulse rounded bg-line/60" />
            </div>
            <div className="h-3 w-3/4 animate-pulse rounded bg-line/60" />
          </div>
        ))}
      </div>
    </Card>
  );
}

function EmptyState({ hasFilters }: { hasFilters: boolean }) {
  return (
    <Card className="flex flex-col items-center px-6 py-16 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-line bg-surface-raised">
        <FileClock className="h-5 w-5 text-ink-subtle" aria-hidden="true" />
      </span>
      <h2 className="mt-4 text-base font-semibold text-ink">
        {hasFilters ? 'No audit records match these filters' : 'No audit records yet'}
      </h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-ink-muted">
        {hasFilters
          ? 'Nothing matches the current filter. Try widening it.'
          : 'Run a recovery analysis and every step Revora takes will be recorded here.'}
      </p>
      {!hasFilters ? (
        <Button asChild className="mt-5">
          <Link href="/batch">Run a recovery analysis</Link>
        </Button>
      ) : null}
    </Card>
  );
}

function ErrorState({ error, onRetry, busy }: { error: ApiError; onRetry: () => void; busy: boolean }) {
  return (
    <Card className="border-unrecoverable/25">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-start">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-unrecoverable/10 ring-1 ring-unrecoverable/20">
          <AlertCircle className="h-5 w-5 text-unrecoverable" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink">
            Your audit trail could not be loaded
          </h2>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
            Revora did not change anything. This was a problem reading the log, not
            recovering money.
          </p>
          <Button variant="secondary" size="sm" className="mt-4" onClick={onRetry} disabled={busy}>
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
