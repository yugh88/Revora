'use client';

import * as React from 'react';
import Link from 'next/link';
import { useParams, useSearchParams } from 'next/navigation';
import { AlertCircle, ArrowLeft, Loader2, RotateCcw, SearchX } from 'lucide-react';

import { AuditTimeline } from '../../../components/AuditTimeline';
import { EventDrilldown } from '../../../components/EventDrilldown';
import { Button } from '../../../components/ui/button';
import { Card } from '../../../components/ui/card';
import { AppShell } from '../../../components/ui/site-header';
import { api, ApiError } from '../../../lib/api-client';
import type { EventDetailResponse } from '../../../lib/types';

/**
 * One event, end to end. BUILD_SPEC Section 13, page 2 drill-down.
 *
 * There are no action buttons on this page, deliberately. The backend exposes
 * no mutation for an individual event — no retry, no resolve, no re-diagnose —
 * so a button offering any of those would be a lie about what the product can
 * do. When such an endpoint exists, the button can be added with it.
 */
/** Known navigation origins. Anything else falls back to Events. */
const ORIGINS: Record<string, { href: string; label: string }> = {
  events: { href: '/events', label: 'Back to Events' },
  audit: { href: '/audit', label: 'Back to Audit' },
  scripts: { href: '/scripts', label: 'Back to Recovery Messages' },
  batch: { href: '/batch', label: 'Back to Run Recovery' },
  promises: { href: '/promises', label: 'Back to Promises to Pay' },
  communications: { href: '/communications', label: 'Back to Communications' },
};

export default function EventDetailPage() {
  const params = useParams<{ id: string }>();
  const eventId = typeof params?.id === 'string' ? params.id : '';

  // Where the user came from, so "Back" tells the truth. Falls back to Events
  // for a direct link or a bookmark, which is the only sensible default when
  // there is no origin to honour.
  const searchParams = useSearchParams();
  const origin = ORIGINS[searchParams.get('from') ?? ''] ?? ORIGINS.events;

  const [detail, setDetail] = React.useState<EventDetailResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<ApiError | null>(null);

  const load = React.useCallback(async () => {
    if (!eventId) return;
    setLoading(true);
    setError(null);
    try {
      setDetail(await api.getEvent(eventId));
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught : new ApiError('Could not load this event.'),
      );
    } finally {
      setLoading(false);
    }
  }, [eventId]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const notFound = error?.status === 404;

  return (
    <AppShell>

      <main className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up">
          <Button asChild variant="ghost" size="sm" className="-ml-2">
            <Link href={origin.href}>
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
              {origin.label}
            </Link>
          </Button>

          <div className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="text-2xl font-semibold tracking-tight text-ink">
              Recovery details
            </h1>
          </div>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
            What happened, why, what Revora decided to do about it, and how it turned
            out.
          </p>
        </div>

        <div className="animate-fade-up stagger-1 mt-6">
          {loading && !detail ? (
            <DetailSkeleton />
          ) : notFound ? (
            <NotFoundState eventId={eventId} />
          ) : error ? (
            <ErrorState error={error} onRetry={() => void load()} busy={loading} />
          ) : detail ? (
            <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
              <div className="space-y-5 xl:col-span-2">
                <EventDrilldown detail={detail} />
              </div>
              <div className="xl:col-span-1">
                <AuditTimeline
                  entries={detail.audit}
                  stagesPresent={detail.stages_present}
                  stagesMissing={detail.stages_missing}
                />
              </div>
            </div>
          ) : null}
        </div>
      </main>
    </AppShell>
  );
}

function DetailSkeleton() {
  return (
    <div className="space-y-5" role="status" aria-busy="true">
      <span className="sr-only">Loading this recovery</span>
      {[0, 1, 2].map((index) => (
        <Card key={index} className="p-5">
          <div className="h-3.5 w-40 animate-pulse rounded bg-line/60" />
          <div className="mt-2 h-3 w-64 animate-pulse rounded bg-line/60" />
          <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
            {[0, 1, 2, 3, 4, 5, 6, 7].map((cell) => (
              <div key={cell}>
                <div className="h-2.5 w-16 animate-pulse rounded bg-line/60" />
                <div className="mt-1.5 h-3 w-24 animate-pulse rounded bg-line/60" />
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}

function NotFoundState({ eventId }: { eventId: string }) {
  return (
    <Card className="flex flex-col items-center px-6 py-16 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-line bg-surface-raised">
        <SearchX className="h-5 w-5 text-ink-subtle" aria-hidden="true" />
      </span>
      <h2 className="mt-4 text-base font-semibold text-ink">
        This recovery is no longer available
      </h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-ink-muted">
        It may belong to an earlier demonstration run. Your current recoveries are all
        listed under Revenue Recovery.
      </p>
      <Button asChild variant="secondary" className="mt-5">
        <Link href="/events">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back to Events
        </Link>
      </Button>
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
            This recovery could not be loaded
          </h2>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
            Revora did not change anything. This was a problem reading the case, not
            working it.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" onClick={onRetry} disabled={busy}>
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Try again
            </Button>
            <Button asChild variant="ghost" size="sm">
              <Link href="/events">Back to Events</Link>
            </Button>
          </div>
        </div>
      </div>
    </Card>
  );
}
