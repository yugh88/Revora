'use client';

import * as React from 'react';
import {
  Activity,
  AlertCircle,
  BadgeIndianRupee,
  Loader2,
  Play,
  RotateCcw,
  ShieldAlert,
  Target,
  TrendingUp,
  Wifi,
  WifiOff,
} from 'lucide-react';

import { DirectionBreakdown } from '../components/DirectionBreakdown';
import { KpiCard, type KpiTrend } from '../components/KpiCard';
import { RecoveryChart } from '../components/RecoveryChart';
import { DashboardSkeleton } from '../components/SkeletonLoader';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { ThemeToggle } from '../components/ui/theme-toggle';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { cn } from '../components/ui/utils';
import {
  api,
  ApiError,
  BACKEND_URL,
  formatClock,
  formatCount,
  formatInr,
  formatInrExact,
  formatPercent,
} from '../lib/api-client';
import type { AnalysisRun, BatchResponse } from '../lib/types';

/**
 * Revora command centre — BUILD_SPEC Section 13, page 1.
 *
 * Every number rendered here comes from a real POST /batch response. There are
 * no placeholder figures anywhere: before the first run the page shows an empty
 * state, not zeros dressed up as data.
 *
 * Why the dashboard is action-driven rather than auto-loading: the only endpoint
 * that returns money aggregates is POST /batch, and POST /batch MUTATES — it
 * runs the full detect → diagnose → decide → policy → execute → verify pipeline
 * and writes the ledger. Silently firing that on page load would mean visiting a
 * dashboard changed the data it was reporting on. So the user presses the
 * button. A read-only aggregate endpoint belongs to a later session; this
 * session does not add one.
 */

const RUN_SIZES = [50, 500] as const;
type RunSize = (typeof RUN_SIZES)[number];

type Connectivity = 'checking' | 'online' | 'offline';

function percentChange(current: number, previous: number): number {
  if (previous === 0) return current === 0 ? 0 : 100;
  return ((current - previous) / Math.abs(previous)) * 100;
}

/**
 * Build a trend only when a previous run genuinely exists.
 *
 * `higherIsBetter` separates the direction of change from whether it is good:
 * amount at risk falling is good, amount recovered falling is not.
 */
function buildTrend(
  current: number,
  previous: number | null,
  higherIsBetter: boolean,
): KpiTrend | null {
  if (previous === null) return null;
  const change = percentChange(current, previous);
  const direction = Math.abs(change) < 0.05 ? 'flat' : change > 0 ? 'up' : 'down';
  return {
    percent: change,
    direction,
    isGood: direction === 'flat' ? true : higherIsBetter ? change > 0 : change < 0,
    label: 'vs previous run',
  };
}

export default function DashboardPage() {
  const [runs, setRuns] = React.useState<AnalysisRun[]>([]);
  const [isRunning, setIsRunning] = React.useState(false);
  const [error, setError] = React.useState<ApiError | null>(null);
  const [size, setSize] = React.useState<RunSize>(50);
  const [connectivity, setConnectivity] = React.useState<Connectivity>('checking');

  const latest: BatchResponse | null = runs.length ? runs[runs.length - 1].response : null;
  const previous: BatchResponse | null =
    runs.length > 1 ? runs[runs.length - 2].response : null;

  /** Read-only liveness probe. Safe to run on mount — it mutates nothing. */
  const checkHealth = React.useCallback(async () => {
    setConnectivity('checking');
    try {
      await api.health();
      setConnectivity('online');
    } catch {
      setConnectivity('offline');
    }
  }, []);

  React.useEffect(() => {
    void checkHealth();
  }, [checkHealth]);

  const runAnalysis = React.useCallback(async () => {
    setIsRunning(true);
    setError(null);
    try {
      const response = await api.runBatch({ count: size });
      setRuns((current) => [
        ...current,
        {
          index: current.length + 1,
          ranAt: formatClock(response.finished_at),
          response,
        },
      ]);
      setConnectivity('online');
    } catch (caught) {
      const apiError =
        caught instanceof ApiError
          ? caught
          : new ApiError('Something went wrong while running the analysis.');
      setError(apiError);
      if (apiError.isNetwork) setConnectivity('offline');
    } finally {
      setIsRunning(false);
    }
  }, [size]);

  const money = latest?.money;
  const activeInterventions = latest
    ? (latest.status_breakdown.intervening ?? 0) + (latest.status_breakdown.escalated ?? 0)
    : 0;
  const previousActive = previous
    ? (previous.status_breakdown.intervening ?? 0) + (previous.status_breakdown.escalated ?? 0)
    : null;

  return (
    <div className="min-h-screen">
      {/* ------------------------------------------------------------------ */}
      {/* Header                                                              */}
      {/* ------------------------------------------------------------------ */}
      <header className="sticky top-0 z-40 border-b border-line bg-bg/85 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1400px] items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <RevoraMark />
            <div className="leading-none">
              <p className="text-[15px] font-semibold tracking-tight text-ink">Revora</p>
              <p className="mt-1 hidden text-micro uppercase text-ink-subtle sm:block">
                Revenue recovery engine
              </p>
            </div>
          </div>

          {/* No navigation links: /events, /batch and the rest are later
              sessions and do not exist. Rendering them as dead links would be
              worse than not rendering them. */}
          <div className="flex items-center gap-2">
            <ConnectivityPill state={connectivity} onRetry={checkHealth} />
            <ThemeToggle />
          </div>
        </div>
      </header>

      {/* ------------------------------------------------------------------ */}
      {/* Body                                                                */}
      {/* ------------------------------------------------------------------ */}
      <main id="main" className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-[28px]">
              Revenue recovery overview
            </h1>
            <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
              Every figure below is measured from the recovery ledger after a real run of
              the decision engine — detected, diagnosed, gated by policy, executed and
              verified.
            </p>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <SizeSelector value={size} onChange={setSize} disabled={isRunning} />
            <Button
              onClick={() => void runAnalysis()}
              disabled={isRunning}
              aria-label={`Run analysis on ${size} records`}
            >
              {isRunning ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  Running…
                </>
              ) : (
                <>
                  <Play className="h-4 w-4" aria-hidden="true" />
                  {runs.length ? 'Run again' : 'Run analysis'}
                </>
              )}
            </Button>
          </div>
        </div>

        <div className="mt-7">
          {error ? (
            <ErrorState error={error} onRetry={() => void runAnalysis()} busy={isRunning} />
          ) : isRunning && !latest ? (
            <DashboardSkeleton />
          ) : !latest ? (
            <EmptyState onRun={() => void runAnalysis()} size={size} />
          ) : (
            <div className="space-y-6">
              {/* KPI row */}
              <section aria-label="Key metrics">
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                  <div className="animate-fade-up">
                    <KpiCard
                      label="Amount at risk"
                      value={formatInr(money!.amount_at_risk)}
                      exactValue={formatInrExact(money!.amount_at_risk)}
                      context={`across ${formatCount(latest.processed)} processed events`}
                      icon={BadgeIndianRupee}
                      tone="neutral"
                      help="Total value of the events this run detected, summed from the ledger."
                      trend={buildTrend(
                        Number.parseFloat(money!.amount_at_risk),
                        previous ? Number.parseFloat(previous.money.amount_at_risk) : null,
                        false,
                      )}
                    />
                  </div>
                  <div className="animate-fade-up stagger-1">
                    <KpiCard
                      label="Amount recovered"
                      value={formatInr(money!.amount_recovered)}
                      exactValue={formatInrExact(money!.amount_recovered)}
                      context={`${formatCount(latest.status_breakdown.recovered ?? 0)} events settled`}
                      icon={TrendingUp}
                      tone="recovered"
                      help="Money actually collected, summed from recovery-ledger rows. Never estimated."
                      trend={buildTrend(
                        Number.parseFloat(money!.amount_recovered),
                        previous ? Number.parseFloat(previous.money.amount_recovered) : null,
                        true,
                      )}
                    />
                  </div>
                  <div className="animate-fade-up stagger-2">
                    <KpiCard
                      label="Recovery rate"
                      value={formatPercent(latest.recovery_rate)}
                      context="of amount at risk, from ledger state"
                      icon={Target}
                      tone="accent"
                      help="Amount recovered divided by amount at risk. A 100% rate would be a red flag, not a win."
                      trend={buildTrend(
                        latest.recovery_rate,
                        previous ? previous.recovery_rate : null,
                        true,
                      )}
                    />
                  </div>
                  <div className="animate-fade-up stagger-3">
                    <KpiCard
                      label="Active interventions"
                      value={formatCount(activeInterventions)}
                      context="intervening + escalated to a human"
                      icon={Activity}
                      tone="pending"
                      help="Events the engine is still working: an attempt is in flight, or a human has taken it on."
                      trend={buildTrend(activeInterventions, previousActive, false)}
                    />
                  </div>
                </div>
              </section>

              {/* Chart + breakdown */}
              <section
                aria-label="Recovery detail"
                className="animate-fade-up stagger-4 grid grid-cols-1 gap-6 lg:grid-cols-3"
              >
                <div className="lg:col-span-2">
                  <div className="h-[400px]">
                    <RecoveryChart runs={runs} />
                  </div>
                </div>
                <div className="h-[400px]">
                  <DirectionBreakdown result={latest} />
                </div>
              </section>

              <RunFooter result={latest} runs={runs.length} />
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Header pieces                                                              */
/* -------------------------------------------------------------------------- */

function RevoraMark() {
  return (
    <span className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-accent shadow-card">
      {/* An upward recovery arc closing a loop — drawn, not imported, so it
          themes with the accent token. */}
      <svg
        width="20"
        height="20"
        viewBox="0 0 20 20"
        fill="none"
        aria-hidden="true"
        className="text-accent-ink"
      >
        <path
          d="M4 13.5C4 8.5 7.5 5 12.5 5H16"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <path
          d="M12.5 1.8 16 5l-3.5 3.2"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="5" cy="15.5" r="2.2" fill="currentColor" />
      </svg>
    </span>
  );
}

function ConnectivityPill({
  state,
  onRetry,
}: {
  state: Connectivity;
  onRetry: () => void;
}) {
  const config = {
    checking: { variant: 'neutral' as const, text: 'Checking', Icon: Loader2 },
    online: { variant: 'recovered' as const, text: 'API online', Icon: Wifi },
    offline: { variant: 'unrecoverable' as const, text: 'API offline', Icon: WifiOff },
  }[state];

  const { Icon } = config;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={onRetry}
          aria-label={`Backend status: ${config.text}. Click to re-check.`}
          className="rounded-full outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
        >
          <Badge variant={config.variant} className="cursor-pointer hover:brightness-105">
            <Icon
              className={cn('h-3 w-3', state === 'checking' && 'animate-spin')}
              aria-hidden="true"
            />
            <span className="hidden sm:inline">{config.text}</span>
          </Badge>
        </button>
      </TooltipTrigger>
      <TooltipContent>
        <p className="font-medium text-ink">{config.text}</p>
        <p className="mt-0.5 break-all text-ink-subtle">{BACKEND_URL}</p>
        <p className="mt-1 text-ink-muted">Click to re-check.</p>
      </TooltipContent>
    </Tooltip>
  );
}

function SizeSelector({
  value,
  onChange,
  disabled,
}: {
  value: RunSize;
  onChange: (size: RunSize) => void;
  disabled: boolean;
}) {
  // A segmented control rather than a dropdown: there are exactly two legal
  // sizes (Section 10), and both are worth seeing at once.
  return (
    <div
      role="group"
      aria-label="Records per analysis"
      className="flex items-center rounded-lg border border-line bg-surface p-0.5"
    >
      {RUN_SIZES.map((option) => {
        const active = option === value;
        return (
          <button
            key={option}
            type="button"
            disabled={disabled}
            aria-pressed={active}
            onClick={() => onChange(option)}
            className={cn(
              'tabular rounded-[6px] px-2.5 py-1 text-xs font-medium transition-all duration-150',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
              'disabled:cursor-not-allowed disabled:opacity-50',
              active
                ? 'bg-accent text-accent-ink shadow-card'
                : 'text-ink-subtle hover:text-ink',
            )}
          >
            {option}
          </button>
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* States                                                                      */
/* -------------------------------------------------------------------------- */

function EmptyState({ onRun, size }: { onRun: () => void; size: RunSize }) {
  return (
    <Card className="animate-fade-up overflow-hidden">
      <div className="relative flex flex-col items-center px-6 py-16 text-center">
        {/* One very soft accent wash, sized and clipped. Not a full-bleed
            gradient. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -top-24 left-1/2 h-56 w-[28rem] -translate-x-1/2 rounded-full bg-accent/[0.07] blur-3xl"
        />
        <span className="relative flex h-14 w-14 items-center justify-center rounded-2xl border border-line bg-surface-raised shadow-card">
          <Activity className="h-6 w-6 text-accent" aria-hidden="true" />
        </span>

        <h2 className="relative mt-5 text-lg font-semibold tracking-tight text-ink">
          No analysis run yet
        </h2>
        <p className="relative mt-2 max-w-md text-sm leading-relaxed text-ink-muted">
          Revora has nothing to report until the engine has actually run. Start an
          analysis and {size} synthetic records will pass through detection, diagnosis,
          the policy gate, execution and verification — then this page fills with the
          measured result.
        </p>

        <div className="relative mt-6">
          <Button onClick={onRun}>
            <Play className="h-4 w-4" aria-hidden="true" />
            Run analysis on {size} records
          </Button>
        </div>

        <p className="relative mt-5 max-w-sm text-xs leading-relaxed text-ink-subtle">
          Nothing is pre-filled and nothing is estimated. Every figure that appears will
          be read back out of the recovery ledger.
        </p>
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
    <Card className="animate-fade-up border-unrecoverable/25">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-start">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-unrecoverable/10 ring-1 ring-unrecoverable/20">
          <AlertCircle className="h-5 w-5 text-unrecoverable" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink">Analysis could not be completed</h2>
          {/* The backend's own sentence, never a stack trace. */}
          <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
            {error.userMessage}
          </p>
          {error.status > 0 ? (
            <p className="tabular mt-2 text-micro uppercase text-ink-subtle">
              HTTP {error.status}
            </p>
          ) : null}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button onClick={onRetry} disabled={busy} variant="secondary" size="sm">
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Try again
            </Button>
            <span className="text-xs text-ink-subtle">
              Backend expected at <code className="text-ink-muted">{BACKEND_URL}</code>
            </span>
          </div>
        </div>
      </div>
    </Card>
  );
}

/**
 * Run provenance.
 *
 * Not decoration: it states which run these figures came from, how many records
 * were isolated as failures, and that run history is session-only. A dashboard
 * that shows numbers without saying where they came from is harder to trust,
 * not easier.
 */
function RunFooter({ result, runs }: { result: BatchResponse; runs: number }) {
  const items: Array<{ label: string; value: string; tone?: 'warn' }> = [
    { label: 'Records', value: formatCount(result.total_records) },
    { label: 'Processed', value: formatCount(result.processed) },
    {
      label: 'Isolated failures',
      value: formatCount(result.isolated_failures),
      tone: result.isolated_failures > 0 ? 'warn' : undefined,
    },
    { label: 'Duplicates skipped', value: formatCount(result.skipped_duplicates) },
    { label: 'Duration', value: `${result.duration_seconds.toFixed(1)}s` },
    { label: 'Gateway', value: result.gateway.replace(/_/g, ' ') },
  ];

  return (
    <Card className="animate-fade-up stagger-4 bg-surface/60">
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3 px-5 py-3.5">
        {items.map((item) => (
          <div key={item.label} className="flex items-baseline gap-2">
            <span className="text-micro uppercase text-ink-subtle">{item.label}</span>
            <span
              className={cn(
                'tabular text-xs font-semibold',
                item.tone === 'warn' ? 'text-pending' : 'text-ink',
              )}
            >
              {item.value}
            </span>
          </div>
        ))}
        <div className="ml-auto flex items-center gap-1.5 text-micro uppercase text-ink-subtle">
          <ShieldAlert className="h-3 w-3" aria-hidden="true" />
          {runs === 1 ? 'Run history is session-only' : `${runs} runs this session`}
        </div>
      </div>
    </Card>
  );
}
