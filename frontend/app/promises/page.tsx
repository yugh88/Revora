'use client';

import * as React from 'react';
import Link from 'next/link';
import {
  AlertCircle,
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  HandCoins,
  Loader2,
  RotateCcw,
  X,
} from 'lucide-react';

import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { AppShell } from '../../components/ui/site-header';
import { cn } from '../../components/ui/utils';
import {
  api,
  ApiError,
  formatCount,
  formatDateTime,
  formatInr,
  formatInrExact,
} from '../../lib/api-client';
import { caseKind, promiseStatusLabel, promiseStatusMeaning } from '../../lib/labels';
import type { EventSummary, PromiseListResponse, PromiseOut } from '../../lib/types';

/**
 * Promises to Pay.
 *
 * A customer who cannot pay today can say when they will. Tracking that is the
 * difference between a customer who is engaged and one who has gone quiet —
 * and chasing the first as though they were the second is how recovery turns
 * into harassment.
 *
 * Three things live on this page, in the order a person needs them: what has
 * been promised, the detail of one promise, and the controls that let a
 * promise be exercised end to end during a demonstration.
 *
 * Nothing here moves money. Recording a promise contacts nobody. "Payment
 * received" does not charge anything — it records the consequence of a payment
 * the simulation has confirmed, written to the ledger by the same code that
 * writes every other recovery.
 */

/** Reporting windows, matching the shape used on Overview. */
const PROMISE_WINDOWS = [
  { value: '1', label: 'Last day' },
  { value: '7', label: 'Last week' },
  { value: '30', label: 'Last month' },
  { value: '90', label: 'Last 3 months' },
  { value: '180', label: 'Last 6 months' },
  { value: '365', label: 'Last 12 months' },
  { value: '', label: 'All time' },
] as const;

function promiseSince(days: string): string | undefined {
  if (!days) return undefined;
  const from = new Date();
  from.setDate(from.getDate() - Number.parseInt(days, 10));
  return from.toISOString();
}

const STATUS_TONE: Record<string, React.ComponentProps<typeof Badge>['variant']> = {
  promised: 'accent',
  due_soon: 'pending',
  fulfilled: 'recovered',
  overdue: 'unrecoverable',
  cancelled: 'stopped',
};

export default function PromisesPage() {
  const [data, setData] = React.useState<PromiseListResponse | null>(null);
  const [selected, setSelected] = React.useState<PromiseOut | null>(null);
  const [since, setSince] = React.useState('');
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<ApiError | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const body = await api.listPromises(undefined, promiseSince(since));
      setData(body);
      setSelected((current) =>
        current ? (body.items.find((p) => p.id === current.id) ?? null) : null,
      );
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught : new ApiError('Promises could not be loaded.'),
      );
    } finally {
      setLoading(false);
    }
  }, [since]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const act = React.useCallback(
    async (fn: () => Promise<unknown>, message: string) => {
      setBusy(true);
      setNotice(null);
      try {
        await fn();
        await load();
        setNotice(message);
      } catch (caught) {
        setError(
          caught instanceof ApiError ? caught : new ApiError('That could not be completed.'),
        );
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const items = data?.items ?? [];

  return (
    <AppShell>
      <main className="mx-auto max-w-[1240px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-ink">
              Promises to pay
            </h1>
            <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
              Customers who have told you when they expect to pay. Revora pauses recovery
              until the promised date, then checks whether the payment arrived.
            </p>
          </div>
          <div className="flex flex-wrap items-end gap-6">
            <label>
              <span className="sr-only">Reporting period</span>
              <select
                value={since}
                onChange={(event) => setSince(event.target.value)}
                className="h-9 cursor-pointer rounded-lg border border-line bg-surface px-3 text-xs text-ink outline-none hover:border-line-strong focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
              >
                {PROMISE_WINDOWS.map((option) => (
                  <option key={option.value || 'all'} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          {data && data.total > 0 ? (
            <div className="flex gap-8">
              <Figure label="Promised" value={formatInr(data.total_promised)} />
              <Figure
                label="Fulfilled"
                value={formatInr(data.total_fulfilled)}
                tone="recovered"
              />
            </div>
          ) : null}
          </div>
        </div>

        {notice ? (
          <p className="animate-fade-up mt-4 flex items-center gap-2 rounded-lg border border-recovered/25 bg-recovered/5 px-3.5 py-2.5 text-xs text-ink-muted">
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-recovered" aria-hidden="true" />
            {notice}
          </p>
        ) : null}

        <div className="mt-6 grid grid-cols-1 gap-5 lg:grid-cols-3">
          {/* ---------------- List ---------------- */}
          <div className="lg:col-span-2">
            {error ? (
              <ErrorState onRetry={() => void load()} busy={loading} />
            ) : loading && !data ? (
              <ListSkeleton />
            ) : items.length === 0 ? (
              <EmptyState />
            ) : (
              <Card className="overflow-hidden">
                <ul className="divide-y divide-line">
                  {items.map((promise) => (
                    <li key={promise.id}>
                      <button
                        type="button"
                        onClick={() => setSelected(promise)}
                        aria-current={selected?.id === promise.id ? 'true' : undefined}
                        className={cn(
                          'block w-full px-4 py-3.5 text-left outline-none transition-colors',
                          'focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent',
                          selected?.id === promise.id
                            ? 'bg-accent/[0.06]'
                            : 'hover:bg-surface-raised/70',
                        )}
                      >
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                          <span className="text-sm font-medium text-ink">
                            {promise.customer_name}
                          </span>
                          <Badge variant={STATUS_TONE[promise.status] ?? 'neutral'}>
                            {promiseStatusLabel(promise.status)}
                          </Badge>
                          <span
                            className="tabular ml-auto text-sm font-semibold text-ink"
                            title={formatInrExact(promise.promised_amount)}
                          >
                            {formatInr(promise.promised_amount)}
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-ink-subtle">
                          Promised for {formatDateTime(promise.promised_date)} ·{' '}
                          {caseKind(promise.event_type)}
                        </p>
                      </button>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>

          {/* ---------------- Detail / create ---------------- */}
          <div className="lg:col-span-1">
            {selected ? (
              <PromiseDetail
                promise={selected}
                busy={busy}
                onFulfil={() =>
                  void act(
                    () => api.fulfilPromise(selected.id),
                    `Payment verified. ${formatInr(selected.promised_amount)} recorded as recovered.`,
                  )
                }
                onCancel={() =>
                  void act(() => api.cancelPromise(selected.id), 'Promise withdrawn.')
                }
                onClose={() => setSelected(null)}
              />
            ) : (
              <HowPromisesHappen
                busy={busy}
                onEvaluate={() =>
                  void act(
                    () => api.evaluatePromises(),
                    'Promises past their date have been recorded as overdue.',
                  )
                }
              />
            )}
          </div>
        </div>
      </main>
    </AppShell>
  );
}

/* -------------------------------------------------------------------------- */

function Figure({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: 'recovered';
}) {
  return (
    <div>
      <p className="text-micro font-semibold uppercase tracking-wide text-ink-subtle">
        {label}
      </p>
      <p
        className={cn(
          'tabular mt-1 text-xl font-semibold',
          tone === 'recovered' ? 'text-recovered' : 'text-ink',
        )}
      >
        {value}
      </p>
    </div>
  );
}

function PromiseDetail({
  promise,
  busy,
  onFulfil,
  onCancel,
  onClose,
}: {
  promise: PromiseOut;
  busy: boolean;
  onFulfil: () => void;
  onCancel: () => void;
  onClose: () => void;
}) {
  const open = promise.status !== 'fulfilled' && promise.status !== 'cancelled';

  return (
    <Card className="sticky top-24">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle>{promise.customer_name}</CardTitle>
            <CardDescription>{caseKind(promise.event_type)}</CardDescription>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </div>
      </CardHeader>

      <div className="px-5 pb-5">
        <p
          className="tabular text-3xl font-semibold tracking-tight text-ink"
          title={formatInrExact(promise.promised_amount)}
        >
          {formatInr(promise.promised_amount)}
        </p>
        <p className="mt-1.5 text-sm text-ink-muted">
          Promised for {formatDateTime(promise.promised_date)}
        </p>

        <div className="mt-4">
          <Badge variant={STATUS_TONE[promise.status] ?? 'neutral'}>
            {promiseStatusLabel(promise.status)}
          </Badge>
          <p className="mt-2 text-xs leading-relaxed text-ink-muted">
            {promiseStatusMeaning(promise.status)}
          </p>
        </div>

        {promise.recovered ? (
          <p className="mt-3 rounded-lg border border-recovered/25 bg-recovered/5 px-3 py-2 text-xs leading-relaxed text-ink-muted">
            <span className="font-medium text-recovered">
              {formatInr(promise.amount_recovered)} recovered
            </span>{' '}
            and recorded against this case.
          </p>
        ) : null}

        <dl className="mt-4 space-y-2.5 border-t border-line pt-4">
          <Row label="Amount at risk" value={formatInr(promise.amount_at_risk)} />
          <Row label="Promise made" value={formatDateTime(promise.created_at)} />
          {promise.resolved_at ? (
            <Row label="Settled" value={formatDateTime(promise.resolved_at)} />
          ) : null}
        </dl>

        <div className="mt-4 border-t border-line pt-4">
          <Link
            href={`/events/${promise.event_id}?from=promises`}
            className="inline-flex items-center gap-1 text-xs text-accent outline-none hover:underline focus-visible:ring-2 focus-visible:ring-accent"
          >
            Open the recovery case
            <ArrowRight className="h-3 w-3" aria-hidden="true" />
          </Link>
        </div>

        {open ? (
          <div className="mt-4 rounded-lg border border-line bg-surface-raised/50 p-3">
            <p className="text-micro font-semibold uppercase text-ink-subtle">
              Demo controls — simulation only
            </p>
            <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
              No customer is contacted and no real payment is made. Recording a payment
              writes the recovery to the ledger exactly as a completed recovery would.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" onClick={onFulfil} disabled={busy}>
                {busy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                Payment received
              </Button>
              <Button variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
                Withdraw promise
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-ink-subtle">{label}</dt>
      <dd className="tabular text-xs font-medium text-ink">{value}</dd>
    </div>
  );
}

/**
 * The customer side of the flow, simulated.
 *
 * Written as a customer would meet it — "Need more time to pay?" — rather than
 * as a form over a database table, because that is what it stands in for.
 */
/**
 * Where promises come from.
 *
 * Deliberately NOT a form. A promise is something a customer says during a
 * recovery conversation, and a merchant typing "customer promises to pay X by
 * Y" on their behalf would be recording an intention nobody expressed. The
 * route to a promise is a simulated customer response on a recovery message.
 */
function HowPromisesHappen({ busy, onEvaluate }: { busy: boolean; onEvaluate: () => void }) {
  const steps = [
    'Revora decides a customer should be contacted',
    'It writes the recovery message',
    'The message is sent in the demo',
    'The customer replies "I\u2019ll pay by\u2026"',
    'The promise appears here',
  ];

  return (
    <Card className="sticky top-24">
      <CardHeader>
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent ring-1 ring-accent/20">
            <HandCoins className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          <div>
            <CardTitle>Where promises come from</CardTitle>
            <CardDescription>
              A promise is something a customer tells you, not something you enter for
              them.
            </CardDescription>
          </div>
        </div>
      </CardHeader>

      <div className="px-5 pb-5">
        <ol className="space-y-2">
          {steps.map((step, index) => (
            <li key={step} className="flex gap-2.5 text-xs leading-relaxed text-ink-muted">
              <span className="tabular mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-surface-raised text-micro font-semibold text-ink-subtle">
                {index + 1}
              </span>
              {step}
            </li>
          ))}
        </ol>

        <Button asChild className="mt-4 w-full">
          <Link href="/communications">
            Start a recovery conversation
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Link>
        </Button>

        <div className="mt-4 border-t border-line pt-3">
          <p className="text-micro font-semibold uppercase text-ink-subtle">
            Demo controls — simulation only
          </p>
          <Button
            variant="secondary"
            size="sm"
            className="mt-2 w-full"
            onClick={onEvaluate}
            disabled={busy}
          >
            Check for overdue promises
          </Button>
        </div>
      </div>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */

function ListSkeleton() {
  return (
    <Card className="p-4" role="status" aria-busy="true">
      <span className="sr-only">Loading promises</span>
      <div className="space-y-4">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="space-y-1.5">
            <div className="flex items-center gap-3">
              <div className="h-3.5 w-32 animate-pulse rounded bg-line/60" />
              <div className="h-4 w-20 animate-pulse rounded-full bg-line/60" />
              <div className="ml-auto h-3.5 w-20 animate-pulse rounded bg-line/60" />
            </div>
            <div className="h-3 w-48 animate-pulse rounded bg-line/60" />
          </div>
        ))}
      </div>
    </Card>
  );
}

function EmptyState() {
  return (
    <Card className="flex flex-col items-center px-6 py-16 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-line bg-surface-raised">
        <HandCoins className="h-5 w-5 text-ink-subtle" aria-hidden="true" />
      </span>
      <h2 className="mt-4 text-base font-semibold text-ink">No promises to pay yet</h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-ink-muted">
        When a customer tells Revora they need more time, the date they commit to appears
        here and recovery pauses until then.
      </p>
    </Card>
  );
}

function ErrorState({ onRetry, busy }: { onRetry: () => void; busy: boolean }) {
  return (
    <Card className="border-unrecoverable/25">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-start">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-unrecoverable/10 ring-1 ring-unrecoverable/20">
          <AlertCircle className="h-5 w-5 text-unrecoverable" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink">Promises could not be loaded</h2>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
            Nothing was changed. This was a problem reading your promises, not recording
            or settling one.
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
