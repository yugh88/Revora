'use client';

import * as React from 'react';
import Link from 'next/link';
import {
  Activity,
  AlertCircle,
  ArrowRight,
  Download,
  Loader2,
  Play,
  RotateCcw,
} from 'lucide-react';

import { RecoveryChart } from '../components/RecoveryChart';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { AppShell } from '../components/ui/site-header';
import { cn } from '../components/ui/utils';
import { LiveIndicator } from '../components/ui/live-status';
import {
  api,
  ApiError,
  LIVE_REFRESH_MS,
  type LiveStatus,
  formatCount,
  formatInr,
  formatInrCompact,
  formatInrExact,
  formatPercent,
  monthsOfHistory,
  PERIODS,
  periodWindow,
  trendBuckets,
  type PeriodKey,
} from '../lib/api-client';
import { eventTypeLabel, promiseStatusLabel } from '../lib/labels';
import {
  EVENT_TYPES,
  type EventListResponse,
  type EventMoneySummary,
  type EventType,
  type PromiseListResponse,
} from '../lib/types';

/**
 * Overview — the answer to "what is happening to my money?"
 *
 * One story, told once: revenue was at risk, Revora intervened, this much came
 * back. Recovered rupees are the largest thing on the page because that is the
 * outcome the product exists to produce.
 *
 * Every figure is computed by the backend from the recovery ledger. The period
 * selector sends a real date window to the API, which recomputes the amounts
 * server-side — nothing is filtered or re-added in the browser, so there is one
 * definition of "recovery rate" in the product and the selector cannot drift
 * out of agreement with the data it labels.
 */

interface DirectionRow {
  type: EventType;
  cases: number;
  atRisk: string;
  recovered: string;
  rate: number;
}

export interface TrendPoint {
  label: string;
  atRisk: number;
  recovered: number;
  rate: number;
}

export default function OverviewPage() {
  const [period, setPeriod] = React.useState<PeriodKey>('all_time');
  const [summary, setSummary] = React.useState<EventListResponse | null>(null);
  const [directions, setDirections] = React.useState<DirectionRow[]>([]);
  const [trend, setTrend] = React.useState<TrendPoint[]>([]);
  const [promises, setPromises] = React.useState<PromiseListResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<ApiError | null>(null);
  const [status, setStatus] = React.useState<LiveStatus>('live');
  const [lastUpdated, setLastUpdated] = React.useState<Date | null>(null);

  const load = React.useCallback(async (key: PeriodKey, options?: { quiet?: boolean }) => {
    // A background refresh never shows a spinner and never clears what is on
    // screen. Blanking a dashboard every five seconds would make live updating
    // worse than not having it.
    if (!options?.quiet) setLoading(true);
    const window = periodWindow(key);
    const scope = { detected_from: window.from, detected_to: window.to };

    try {
      const overall = await api.listEvents({ ...scope, limit: 1 });
      setSummary(overall);

      // Per-direction figures, each a real server-side aggregate over the same
      // window — five small queries rather than one client-side regroup, so
      // these and the recovery feed cannot disagree.
      const perDirection = await Promise.all(
        EVENT_TYPES.map(async (type) => {
          const body = await api.listEvents({ ...scope, type, limit: 1 });
          return {
            type,
            cases: body.total,
            atRisk: body.money.amount_at_risk,
            recovered: body.money.amount_recovered,
            rate: body.money.recovery_rate,
          };
        }),
      );
      setDirections(perDirection.sort((a, b) => b.cases - a.cases));

      // One query per time bucket. Each returns ledger-computed money for that
      // window, so the trend is measured rather than interpolated.
      const buckets = trendBuckets(key, overall.earliest_detected_at);
      const points = await Promise.all(
        buckets.map(async (bucket) => {
          const body = await api.listEvents({
            detected_from: bucket.from,
            detected_to: bucket.to,
            limit: 1,
          });
          return {
            label: bucket.label,
            atRisk: Number.parseFloat(body.money.amount_at_risk),
            recovered: Number.parseFloat(body.money.amount_recovered),
            rate: body.money.recovery_rate * 100,
          };
        }),
      );
      setTrend(points);

      try {
        // Same window as every other figure on the page, so the promise totals
        // and the recovery totals describe the same period. Previously this was
        // all-time and had to be labelled as such — now it moves with the
        // selector like everything else.
        setPromises(await api.listPromises(undefined, window.from));
      } catch {
        setPromises(null);  // absence is not an error worth blocking the page
      }
      setStatus('live');
      setLastUpdated(new Date());
      setError(null);
    } catch (caught) {
      // Keep the previous figures. Stale numbers beat an empty page.
      setStatus('reconnecting');
      if (!options?.quiet) {
        setError(
          caught instanceof ApiError
            ? caught
            : new ApiError('The recovery figures could not be loaded.'),
        );
      }
    } finally {
      setLoading(false);
    }
  }, []);

  // Revora works on its own; this keeps the page in step with it. One shared
  // interval for the whole application, so no two pages can show different
  // numbers for the same moment.
  React.useEffect(() => {
    let cancelled = false;
    const tick = () => {
      if (!cancelled && !document.hidden) void load(period, { quiet: true });
    };
    void load(period);
    const timer = setInterval(tick, LIVE_REFRESH_MS);
    const onVisible = () => {
      if (!document.hidden) void load(period, { quiet: true });
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [load, period]);

  const money = summary?.money ?? null;
  const totalCases = summary?.total ?? 0;
  const hasData = totalCases > 0;
  const history = monthsOfHistory(summary?.earliest_detected_at ?? null);
  const requestedMonths =
    period === 'last_6_months' ? 6 : period === 'last_12_months' ? 12 : 0;
  const historyShortfall = requestedMonths > 0 && history > 0 && history < requestedMonths;

  return (
    <AppShell>
      <main className="mx-auto max-w-[1240px] px-4 py-8 sm:px-6 lg:px-8">
        <ReportHeader period={period} />

        <div className="animate-fade-up flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-[28px]">
              Turn revenue at risk into revenue recovered.
            </h1>
            <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
              Revora finds the payments that failed, the checkouts that stalled and the
              invoices going unpaid — then works each one within your limits until the
              money is back or it is wiser to stop.
            </p>
          </div>
          <div className="no-print flex shrink-0 flex-col items-end gap-2">
            <LiveIndicator status={status} lastUpdated={lastUpdated} />
            <div className="flex items-center gap-2">
            <PeriodSelector value={period} onChange={setPeriod} disabled={loading} />
            {hasData ? (
              <Button
                variant="secondary"
                onClick={() => window.print()}
                aria-label="Download this report as a PDF"
              >
                <Download className="h-4 w-4" aria-hidden="true" />
                Download report
              </Button>
            ) : null}
            </div>
          </div>
        </div>

        {historyShortfall ? (
          <p className="animate-fade-up mt-4 rounded-lg border border-line bg-surface-raised/60 px-3.5 py-2.5 text-xs text-ink-muted">
            Only {history} {history === 1 ? 'month' : 'months'} of recovery history are
            available, so this covers everything recorded so far rather than a full{' '}
            {requestedMonths} months.
          </p>
        ) : null}

        <div className="mt-6">
          {error ? (
            <ErrorState error={error} onRetry={() => void load(period)} busy={loading} />
          ) : loading && !summary ? (
            <OverviewSkeleton />
          ) : !hasData ? (
            <EmptyState />
          ) : (
            <div className="space-y-6">
              <RecoveredHeadline money={money!} cases={totalCases} loading={loading} />
              <RecoveryJourney money={money!} cases={totalCases} />
              {promises && promises.total > 0 ? (
                <PromisesStrip promises={promises} />
              ) : null}

              <section className="grid grid-cols-1 gap-6 lg:grid-cols-3">
                <div className="lg:col-span-2">
                  <div className="h-[360px]">
                    <RecoveryChart trend={trend} />
                  </div>
                </div>
                <div className="lg:col-span-1">
                  <DirectionsPanel rows={directions} />
                </div>
              </section>
            </div>
          )}
        </div>
      </main>
    </AppShell>
  );
}

/* -------------------------------------------------------------------------- */

/**
 * Report masthead. Only visible when printing.
 *
 * The merchant name comes from Settings, which is the only place it is
 * recorded. Everything else on the printed page is the dashboard's own
 * components rendering the dashboard's own data — there is no second assembly
 * of the figures, so the report cannot disagree with the screen.
 */
function ReportHeader({ period }: { period: PeriodKey }) {
  const [business, setBusiness] = React.useState('Revora Demo Merchant');
  const [merchant, setMerchant] = React.useState('');

  React.useEffect(() => {
    try {
      const stored = window.localStorage.getItem('revora.preferences');
      if (stored) {
        const prefs = JSON.parse(stored) as {
          businessName?: string;
          merchantName?: string;
        };
        if (prefs.businessName) setBusiness(prefs.businessName);
        if (prefs.merchantName) setMerchant(prefs.merchantName);
      }
    } catch {
      // Defaults are fine.
    }
  }, []);

  const label = PERIODS.find((option) => option.key === period)?.label ?? 'All time';

  return (
    <div className="print-only mb-6 border-b border-line pb-4">
      <div className="flex items-start justify-between gap-6">
        <div>
          <p className="text-lg font-semibold tracking-tight text-ink">Revora</p>
          <p className="text-xs text-ink-muted">Revenue recovery report</p>
        </div>
        <div className="text-right">
          <p className="text-sm font-medium text-ink">{business}</p>
          {merchant ? <p className="text-xs text-ink-muted">{merchant}</p> : null}
          <p className="mt-1 text-xs text-ink-muted">
            {label} · generated {new Date().toLocaleDateString(undefined, {
              day: 'numeric',
              month: 'long',
              year: 'numeric',
            })}
          </p>
        </div>
      </div>
    </div>
  );
}

function PeriodSelector({
  value,
  onChange,
  disabled,
}: {
  value: PeriodKey;
  onChange: (key: PeriodKey) => void;
  disabled: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="Reporting period"
      className="flex shrink-0 items-center rounded-lg border border-line bg-surface p-0.5"
    >
      {PERIODS.map((option) => {
        const active = option.key === value;
        return (
          <button
            key={option.key}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => onChange(option.key)}
            className={cn(
              'whitespace-nowrap rounded-[6px] px-2.5 py-1.5 text-xs font-medium transition-all',
              'outline-none focus-visible:ring-2 focus-visible:ring-accent',
              'disabled:cursor-not-allowed disabled:opacity-60',
              active
                ? 'bg-accent text-accent-ink shadow-card'
                : 'text-ink-subtle hover:text-ink',
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

function RecoveredHeadline({
  money,
  cases,
  loading,
}: {
  money: EventMoneySummary;
  cases: number;
  loading: boolean;
}) {
  return (
    <Card className={cn('animate-fade-up overflow-hidden', loading && 'opacity-60')}>
      <div className="relative p-6 sm:p-8">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-20 -top-24 h-64 w-80 rounded-full bg-recovered/[0.07] blur-3xl"
        />
        <div className="relative flex flex-wrap items-end justify-between gap-8">
          <div className="min-w-0">
            <p className="text-micro font-semibold uppercase tracking-wide text-ink-subtle">
              Revenue recovered
            </p>
            <p
              className="tabular mt-2 text-[2.75rem] font-semibold leading-none tracking-tight text-recovered sm:text-[3.5rem]"
              title={formatInrExact(money.amount_recovered)}
            >
              {formatInr(money.amount_recovered)}
            </p>
            <p className="mt-3 max-w-xl text-sm leading-relaxed text-ink-muted">
              Revora has won back{' '}
              <span className="font-medium text-ink">{formatInr(money.amount_recovered)}</span>{' '}
              from{' '}
              <span className="font-medium text-ink">{formatInr(money.amount_at_risk)}</span> at
              risk across {formatCount(cases)} {cases === 1 ? 'case' : 'cases'}.
            </p>
          </div>

          <div className="flex shrink-0 flex-wrap gap-10">
            <Figure label="Recovery rate" value={formatPercent(money.recovery_rate)} />
            <Figure
              label="Active recoveries"
              value={formatCount(money.active_interventions)}
            />
          </div>
        </div>
      </div>
    </Card>
  );
}

function Figure({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div title={hint}>
      <p className="text-micro font-semibold uppercase tracking-wide text-ink-subtle">
        {label}
      </p>
      <p className="tabular mt-1.5 text-2xl font-semibold text-ink">{value}</p>
    </div>
  );
}

/**
 * The recovery lifecycle, and where the money currently sits.
 *
 * The three amounts sum exactly to the amount at risk — an identity the backend
 * guarantees to the paisa — so the bar is a true partition of the money rather
 * than three unrelated figures placed side by side.
 */
function RecoveryJourney({ money, cases }: { money: EventMoneySummary; cases: number }) {
  const atRisk = Number.parseFloat(money.amount_at_risk);
  const share = (raw: string) => (atRisk > 0 ? (Number.parseFloat(raw) / atRisk) * 100 : 0);

  const segments = [
    { key: 'recovered', label: 'Recovered', raw: money.amount_recovered, tone: 'bg-recovered' },
    { key: 'pending', label: 'In progress', raw: money.amount_pending, tone: 'bg-pending' },
    { key: 'lost', label: 'Written off', raw: money.amount_lost, tone: 'bg-unrecoverable' },
  ];

  const steps = [
    { label: 'Revenue at risk', value: formatInr(money.amount_at_risk) },
    { label: 'Revora intervenes', value: `${formatCount(cases)} cases` },
    {
      label: 'Customer responds',
      value: `${formatCount(money.active_interventions)} in flight`,
    },
    { label: 'Revenue recovered', value: formatInr(money.amount_recovered), strong: true },
  ];

  return (
    <Card className="animate-fade-up stagger-1">
      <div className="p-6">
        <ol className="flex flex-wrap items-center gap-x-1 gap-y-3">
          {steps.map((step, index) => (
            <li key={step.label} className="flex items-center">
              <div>
                <p className="text-micro uppercase tracking-wide text-ink-subtle">
                  {step.label}
                </p>
                <p
                  className={cn(
                    'tabular mt-0.5 text-sm font-semibold',
                    step.strong ? 'text-recovered' : 'text-ink',
                  )}
                >
                  {step.value}
                </p>
              </div>
              {index < steps.length - 1 ? (
                <ArrowRight
                  className="mx-4 h-3.5 w-3.5 shrink-0 text-ink-subtle/60"
                  aria-hidden="true"
                />
              ) : null}
            </li>
          ))}
        </ol>

        <div className="mt-6 border-t border-line pt-5">
          <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-line/70">
            {segments.map((segment) => (
              <div
                key={segment.key}
                className={cn('h-full transition-all duration-700', segment.tone)}
                // eslint-disable-next-line react/forbid-dom-props
                {...{ style: { width: `${share(segment.raw)}%` } }}
              />
            ))}
          </div>
          <dl className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {segments.map((segment) => (
              <div key={segment.key} className="flex items-baseline gap-2">
                <span
                  aria-hidden="true"
                  className={cn('h-2 w-2 shrink-0 rounded-full', segment.tone)}
                />
                <dt className="text-xs text-ink-muted">{segment.label}</dt>
                <dd className="tabular ml-auto text-xs">
                  <span className="font-semibold text-ink" title={formatInrExact(segment.raw)}>
                    {formatInr(segment.raw)}
                  </span>
                  <span className="ml-1.5 text-ink-subtle">
                    {share(segment.raw).toFixed(0)}%
                  </span>
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </Card>
  );
}

/**
 * Promises to pay, compact.
 *
 * Only figures the promise records actually support: how many are outstanding,
 * how many were kept, how many lapsed, and the amounts behind them. No
 * decorative metric, and no separate money total — the fulfilled amount is the
 * ledger's, reached through the promise rows.
 *
 * Filtered by the same reporting period as the rest of the page: the promises
 * API takes the same date window, so these totals and the recovery totals
 * always describe the same span of time.
 */
function PromisesStrip({ promises }: { promises: PromiseListResponse }) {
  const counts = promises.status_breakdown;
  const states = ['promised', 'due_soon', 'fulfilled', 'overdue'] as const;

  // How much of what was promised has actually arrived, and how much is still
  // owed. A count of promises says nothing about whether the money came in;
  // this is the figure a merchant is actually waiting on.
  const promised = Number.parseFloat(promises.total_promised) || 0;
  const paid = Number.parseFloat(promises.total_fulfilled) || 0;
  const outstanding = Math.max(promised - paid, 0);
  const share = promised > 0 ? (paid / promised) * 100 : 0;

  return (
    <Card className="animate-fade-up stagger-2">
      <div className="flex flex-wrap items-center gap-x-8 gap-y-4 p-5">
        <div className="min-w-[15rem] flex-1">
          <p className="text-micro font-semibold uppercase tracking-wide text-ink-subtle">
            Promises to pay
          </p>
          <p className="mt-1 text-sm text-ink-muted">
            <span className="tabular font-medium text-recovered">
              {formatInr(promises.total_fulfilled)}
            </span>{' '}
            received of{' '}
            <span className="tabular font-medium text-ink">
              {formatInr(promises.total_promised)}
            </span>{' '}
            promised ·{' '}
            <span className="tabular font-medium text-pending">
              {formatInr(String(outstanding))}
            </span>{' '}
            still due
          </p>
          <div className="mt-2 flex h-1.5 w-full overflow-hidden rounded-full bg-line/70">
            <div
              className="h-full rounded-full bg-recovered transition-all duration-700"
              // eslint-disable-next-line react/forbid-dom-props
              {...{ style: { width: `${Math.min(share, 100)}%` } }}
            />
          </div>
          <p className="tabular mt-1 text-micro text-ink-subtle">
            {share.toFixed(0)}% of promised money received
          </p>
        </div>

        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {states.map((state) => (
            <div key={state}>
              <p className="text-micro uppercase text-ink-subtle">
                {promiseStatusLabel(state)}
              </p>
              <p
                className={cn(
                  'tabular mt-0.5 text-lg font-semibold',
                  state === 'fulfilled'
                    ? 'text-recovered'
                    : state === 'overdue'
                      ? 'text-unrecoverable'
                      : 'text-ink',
                )}
              >
                {formatCount(counts[state] ?? 0)}
              </p>
            </div>
          ))}
        </div>

        <Link
          href="/promises"
          className="ml-auto inline-flex items-center gap-1 rounded text-xs text-accent outline-none hover:underline focus-visible:ring-2 focus-visible:ring-accent"
        >
          View promises
          <ArrowRight className="h-3 w-3" aria-hidden="true" />
        </Link>
      </div>
    </Card>
  );
}

function DirectionsPanel({ rows }: { rows: DirectionRow[] }) {
  const totalCases = rows.reduce((sum, row) => sum + row.cases, 0);

  return (
    <Card className="animate-fade-up stagger-2 flex h-[360px] flex-col">
      <div className="p-5 pb-3">
        <h2 className="text-sm font-semibold tracking-tight text-ink">
          Where revenue is at risk
        </h2>
        <p className="mt-1 text-xs leading-relaxed text-ink-subtle">
          {formatCount(totalCases)} {totalCases === 1 ? 'case' : 'cases'} across the ways
          revenue slips away.
        </p>
      </div>

      {totalCases === 0 ? (
        <p className="flex-1 px-5 pb-5 text-xs text-ink-subtle">
          Nothing at risk in this period.
        </p>
      ) : (
        <ul className="flex-1 space-y-3 overflow-y-auto px-5 pb-5">
          {rows.map((row) => (
            <li key={row.type}>
              <Link
                href={`/events?type=${row.type}`}
                className="group block rounded-lg px-2 py-1.5 outline-none transition-colors hover:bg-surface-raised focus-visible:bg-surface-raised focus-visible:ring-2 focus-visible:ring-accent"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-xs font-medium text-ink">
                    {eventTypeLabel(row.type)}
                  </span>
                  <span className="tabular shrink-0 text-micro text-ink-subtle">
                    {formatCount(row.cases)} {row.cases === 1 ? 'case' : 'cases'}
                  </span>
                </div>
                <div className="mt-1 flex items-baseline justify-between gap-3">
                  <span className="tabular text-micro text-ink-subtle">
                    {formatInrCompact(row.atRisk)} at risk
                  </span>
                  {/* Nothing recovered yet is not the same as nothing
                      happening. Showing "₹0 back" against a full bar reads as
                      a broken figure; "in progress" is what is actually true
                      while Revora is still working the category. */}
                  {Number.parseFloat(row.recovered) > 0 ? (
                    <span className="tabular text-micro font-medium text-recovered">
                      {formatInrCompact(row.recovered)} back
                    </span>
                  ) : (
                    <span className="text-micro font-medium text-pending">
                      In progress
                    </span>
                  )}
                </div>
                <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-line/70">
                  <div
                    className="h-full rounded-full bg-recovered transition-all duration-500"
                    // eslint-disable-next-line react/forbid-dom-props
                    {...{ style: { width: `${Math.min(row.rate * 100, 100)}%` } }}
                  />
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/* -------------------------------------------------------------------------- */

function OverviewSkeleton() {
  return (
    <div className="space-y-6" role="status" aria-busy="true">
      <span className="sr-only">Loading your recovery figures</span>
      <Card className="p-8">
        <div className="h-3 w-32 animate-pulse rounded bg-line/60" />
        <div className="mt-3 h-12 w-72 animate-pulse rounded bg-line/60" />
        <div className="mt-4 h-3 w-96 animate-pulse rounded bg-line/60" />
      </Card>
      <Card className="p-6">
        <div className="h-3 w-full animate-pulse rounded bg-line/60" />
        <div className="mt-6 h-2.5 w-full animate-pulse rounded-full bg-line/60" />
      </Card>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="h-[360px] lg:col-span-2" />
        <Card className="h-[360px]" />
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <Card className="no-print animate-fade-up flex flex-col items-center px-6 py-20 text-center">
      <span className="flex h-14 w-14 items-center justify-center rounded-2xl border border-line bg-surface-raised">
        <Activity className="h-6 w-6 text-accent" aria-hidden="true" />
      </span>
      <h2 className="mt-5 text-lg font-semibold tracking-tight text-ink">
        Watching for revenue at risk
      </h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-ink-muted">
        Revora is connected and monitoring. As payments fail, checkouts stall and
        invoices age, it will work each case and the results will appear here — you do
        not need to start anything.
      </p>
      <p className="mt-5 text-xs text-ink-subtle">
        Safe demo environment. No real payments or customer contacts.
      </p>
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
    <Card className="animate-fade-up border-unrecoverable/25">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-start">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-unrecoverable/10 ring-1 ring-unrecoverable/20">
          <AlertCircle className="h-5 w-5 text-unrecoverable" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink">
            Your recovery figures could not be loaded
          </h2>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
            Revora did not change anything. This was a problem reading your figures, not
            recovering money — nothing has been lost.
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
