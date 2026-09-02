'use client';

import * as React from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  BadgeIndianRupee,
  CheckCircle2,
  Database,
  Loader2,
  Play,
  Clock,
  RotateCcw,
  Target,
  TrendingUp,
} from 'lucide-react';

import { DirectionBreakdown } from '../../components/DirectionBreakdown';
import { GatewayToggle } from '../../components/GatewayToggle';
import { KpiCard } from '../../components/KpiCard';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { AppShell } from '../../components/ui/site-header';
import { LiveIndicator } from '../../components/ui/live-status';
import { useLiveRefresh } from '../../components/ui/use-live-data';
import { Tooltip, TooltipContent, TooltipTrigger } from '../../components/ui/tooltip';
import { cn } from '../../components/ui/utils';
import {
  api,
  ApiError,
  formatCount,
  formatDateTime,
  formatInr,
  formatInrExact,
  formatPercent,
} from '../../lib/api-client';
import { gatewayLabel, stopReasonLabel } from '../../lib/labels';
import type { BatchResponse, GatewayUsed, RunSummary } from '../../lib/types';

/**
 * Recovery analysis console. BUILD_SPEC Section 13, page 3.
 *
 * Running a batch MUTATES: it detects, diagnoses, decides, gates, executes,
 * verifies, and writes the ledger and audit trail. The pre-run panel says so in
 * plain language, because an operator pressing a button should know what it
 * costs.
 *
 * Every figure in the results comes from the response. Where the backend
 * genuinely has nothing to report — promises, for instance, whose tracker is a
 * later session — the value is shown as unavailable rather than as a zero that
 * would read as a measurement.
 */

const SIZES = [50, 500] as const;
type RunSize = (typeof SIZES)[number];

/**
 * A merchant-readable name for a run.
 *
 * The engine identifies runs by an opaque batch id; a merchant should not have
 * to read one. The number is the count of runs in this session, and the date
 * makes it locatable in conversation ("the payment recovery we ran on the 27th").
 */
function runName(index: number, at: Date): string {
  const day = at.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  const hour = at.getHours();
  if (index === 1 && hour < 12) return `Morning Recovery Run — ${day}`;
  return `Recovery Run #${index} — ${day}`;
}

export default function BatchPage() {
  return (
    <React.Suspense fallback={null}>
      <RunRecovery />
    </React.Suspense>
  );
}

function RunRecovery() {
  const [size, setSize] = React.useState<RunSize>(50);
  const [runLabel, setRunLabel] = React.useState<string | null>(null);
  const [history, setHistory] = React.useState<RunSummary[]>([]);

  // Reopening is driven by the URL, so a run can be linked to and the browser
  // back button behaves. The identifier lives in the address bar only — it is
  // never rendered as page content.
  const searchParams = useSearchParams();
  const openRunId = searchParams.get('run');
  const [gateway, setGateway] = React.useState<GatewayUsed>('local_simulation');
  const [running, setRunning] = React.useState(false);
  const [result, setResult] = React.useState<BatchResponse | null>(null);
  const [error, setError] = React.useState<ApiError | null>(null);
  const [elapsed, setElapsed] = React.useState(0);

  // A live counter during the run. A 500-record batch takes ~25s and a static
  // spinner for that long is indistinguishable from a hang.
  // Load history on mount so a merchant returning to the page sees earlier
  // runs without having to run anything.
  const loadHistory = React.useCallback(async () => {
    try {
      setHistory((await api.listRuns()).items);
      return true;
    } catch {
      // History is a convenience; its absence must not break the page.
      return false;
    }
  }, []);

  // Runs finish on their own now, so the list has to grow on its own too.
  const { status: liveStatus, lastUpdated } = useLiveRefresh(loadHistory, []);

  // Reopen a stored run when the URL names one.
  React.useEffect(() => {
    if (!openRunId) return;
    let cancelled = false;
    api
      .getRun(openRunId)
      .then((detail) => {
        if (cancelled) return;
        setResult(detail.snapshot);
        setRunLabel(detail.run.name);
        setError(null);
      })
      .catch((caught) => {
        if (!cancelled) {
          setError(
            caught instanceof ApiError
              ? caught
              : new ApiError('That recovery run could not be opened.'),
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [openRunId]);

  React.useEffect(() => {
    if (!running) return;
    setElapsed(0);
    const started = Date.now();
    const timer = setInterval(() => setElapsed((Date.now() - started) / 1000), 200);
    return () => clearInterval(timer);
  }, [running]);

  const run = React.useCallback(async () => {
    // Guard as well as disabling the button: a double submit would run the
    // pipeline twice and write two batches.
    if (running) return;
    setRunning(true);
    setError(null);
    try {
      const response = await api.runBatch({ count: size, gateway });
      setResult(response);
      // The backend names and stores the run; read it back rather than
      // inventing a second name here that could disagree with history.
      const runs = await api.listRuns();
      setHistory(runs.items);
      setRunLabel(runs.items.find((r) => r.id === response.batch_id)?.name ?? null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError('The analysis could not be completed.'),
      );
    } finally {
      setRunning(false);
    }
  }, [running, size, gateway]);

  return (
    <AppShell>

      <main className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">
            Recovery runs
          </h1>
          <LiveIndicator status={liveStatus} lastUpdated={lastUpdated} />
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
            Every batch of cases Revora has worked, and what came back. Recovery happens
            on its own — nothing here needs starting.
          </p>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-5 lg:grid-cols-3">
          {/* ---------------- Configuration ---------------- */}
          <div className="animate-fade-up stagger-1 lg:col-span-1">
            <AutonomousNotice />
            <RunHistory runs={history} openRunId={openRunId} />
          </div>

          {/* ---------------- Results ---------------- */}
          <div className="animate-fade-up stagger-2 lg:col-span-2">
            {running ? (
              <RunningState size={size} gateway={gateway} elapsed={elapsed} />
            ) : error ? (
              <ErrorState
                error={error}
                gateway={gateway}
                onRetry={() => void run()}
                onUseSimulator={() => setGateway('local_simulation')}
              />
            ) : result ? (
              <Results
                result={result}
                label={runLabel}
                runId={result.batch_id}
                openedFromHistory={Boolean(openRunId)}
              />
            ) : (
              <IdleState />
            )}
          </div>
        </div>
      </main>
    </AppShell>
  );
}

/**
 * Recent recovery runs.
 *
 * Each row reopens a stored run rather than re-running the analysis, which is
 * the point: a merchant who ran something on Tuesday should be able to look at
 * Tuesday's result on Thursday without touching the engine again.
 *
 * The figures shown are the ones that run reported at the time. They are not
 * recomputed, so a past run keeps saying what the merchant actually saw.
 */
/**
 * Why there is no button on this page any more.
 *
 * Recovery is not something a merchant launches; Revora processes payment
 * events as they arrive. This page is a record of what it has done, not a
 * control for making it happen.
 */
function AutonomousNotice() {
  return (
    <Card className="mb-5">
      <div className="flex items-start gap-2.5 p-5">
        <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-recovered" aria-hidden="true" />
        <div>
          <p className="text-sm font-semibold text-ink">Recovery is running</p>
          <p className="mt-1 text-xs leading-relaxed text-ink-muted">
            Revora works payment events as they arrive. Runs appear here as they
            complete — there is nothing to start.
          </p>
        </div>
      </div>
    </Card>
  );
}

function RunHistory({
  runs,
  openRunId,
}: {
  runs: RunSummary[];
  openRunId: string | null;
}) {
  if (runs.length === 0) {
    return (
      <Card className="mt-5">
        <div className="p-5">
          <h2 className="text-sm font-semibold tracking-tight text-ink">
            Recent recovery runs
          </h2>
          <p className="mt-1.5 text-xs leading-relaxed text-ink-subtle">
            No recovery runs yet. Once you run one, it is saved here so you can come
            back to the results without running it again.
          </p>
        </div>
      </Card>
    );
  }

  return (
    <Card className="mt-5">
      <div className="p-5 pb-2">
        <h2 className="text-sm font-semibold tracking-tight text-ink">
          Recent recovery runs
        </h2>
        <p className="mt-1 text-xs leading-relaxed text-ink-subtle">
          Reopen a completed run without running it again.
        </p>
      </div>
      <ul className="px-3 pb-4">
        {runs.map((run) => {
          const active = run.id === openRunId;
          return (
            <li key={run.id}>
              <Link
                href={`/batch?run=${encodeURIComponent(run.id)}`}
                aria-current={active ? 'true' : undefined}
                className={cn(
                  'block rounded-lg px-2.5 py-2.5 outline-none transition-colors',
                  'focus-visible:ring-2 focus-visible:ring-accent',
                  active ? 'bg-accent/[0.07] ring-1 ring-accent/25' : 'hover:bg-surface-raised',
                )}
              >
                <p className="truncate text-xs font-medium text-ink">{run.name}</p>
                <p className="mt-0.5 text-micro text-ink-subtle">
                  {formatDateTime(run.finished_at)} · {gatewayLabel(run.gateway)}
                </p>
                <p className="tabular mt-1 text-micro">
                  <span className="font-medium text-recovered">
                    {formatInr(run.amount_recovered)} recovered
                  </span>
                  <span className="text-ink-subtle">
                    {' '}
                    · {formatCount(run.processed)} cases
                  </span>
                </p>
              </Link>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* States                                                                      */
/* -------------------------------------------------------------------------- */

function IdleState() {
  return (
    <Card className="flex h-full flex-col items-center justify-center px-6 py-20 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-line bg-surface-raised">
        <Target className="h-5 w-5 text-ink-subtle" aria-hidden="true" />
      </span>
      <h2 className="mt-4 text-base font-semibold text-ink">Ready when you are</h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-ink-muted">
        Nothing is shown until the engine has run. Results here are read back from the
        recovery ledger, never estimated.
      </p>
    </Card>
  );
}

function RunningState({
  size,
  gateway,
  elapsed,
}: {
  size: number;
  gateway: GatewayUsed;
  elapsed: number;
}) {
  // Honest progress: the API gives no percentage, so this reports the stage the
  // pipeline is most likely in rather than faking a progress bar.
  // Honest staging: the API returns no percentage, so this reports the phase the
  // work is most likely in rather than animating a fake progress bar.
  const stages = [
    'Analysing revenue at risk…',
    'Choosing recovery actions…',
    'Applying recovery policies…',
    'Verifying outcomes…',
  ];
  const stageIndex =
    elapsed < 1.5 ? 0 : elapsed < 4 ? 1 : elapsed < 12 ? 2 : 3;
  const phase = stages[stageIndex];

  return (
    <Card
      className="flex h-full flex-col items-center justify-center px-6 py-20 text-center"
      role="status"
      aria-live="polite"
    >
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-accent/10 ring-1 ring-accent/20">
        <Loader2 className="h-5 w-5 animate-spin text-accent" aria-hidden="true" />
      </span>
      <h2 className="mt-4 text-base font-semibold text-ink">
        Working {formatCount(size)} cases
      </h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-ink-muted">{phase}</p>
      <ol className="mt-5 space-y-1.5">
        {stages.map((stage, index) => (
          <li
            key={stage}
            className={cn(
              'text-xs transition-colors',
              index < stageIndex
                ? 'text-recovered'
                : index === stageIndex
                  ? 'font-medium text-ink'
                  : 'text-ink-subtle/60',
            )}
          >
            {index < stageIndex ? '✓ ' : index === stageIndex ? '→ ' : '   '}
            {stage.replace('…', '')}
          </li>
        ))}
      </ol>
      <p className="tabular mt-5 text-micro uppercase text-ink-subtle">
        {gatewayLabel(gateway)}
      </p>
    </Card>
  );
}

function ErrorState({
  error,
  gateway,
  onRetry,
  onUseSimulator,
}: {
  error: ApiError;
  gateway: GatewayUsed;
  onRetry: () => void;
  onUseSimulator: () => void;
}) {
  const credentialProblem =
    gateway === 'razorpay_test' && error.status === 400;

  return (
    <Card className="border-unrecoverable/25">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-start">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-unrecoverable/10 ring-1 ring-unrecoverable/20">
          <AlertCircle className="h-5 w-5 text-unrecoverable" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink">
            {credentialProblem
              ? 'Razorpay sandbox is not configured'
              : 'Analysis could not be completed'}
          </h2>

          {/* The backend's own sentence, verbatim. It already explains exactly
              what is wrong and how to fix it. */}
          <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
            {error.userMessage}
          </p>

          {credentialProblem ? (
            <p className="mt-3 rounded-lg border border-line bg-surface-raised/60 px-3 py-2 text-xs leading-relaxed text-ink-muted">
              Nothing was run and nothing fell back. Revora does not silently switch to
              the simulator when the sandbox is unavailable — that would let a run report
              simulator numbers as though they came from Razorpay.
            </p>
          ) : null}

          {error.status > 0 ? (
            <p className="tabular mt-2 text-micro uppercase text-ink-subtle">
              HTTP {error.status}
            </p>
          ) : null}

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" onClick={onRetry}>
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              Try again
            </Button>
            {credentialProblem ? (
              <Button variant="ghost" size="sm" onClick={onUseSimulator}>
                Switch to the built-in simulator
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* Results                                                                     */
/* -------------------------------------------------------------------------- */

function Results({
  result,
  label,
  runId,
  openedFromHistory,
}: {
  result: BatchResponse;
  label: string | null;
  runId: string | null;
  openedFromHistory: boolean;
}) {
  const money = result.money;
  const triggers = result.stopping_rule_triggers;
  const triggerRows = [
    { label: 'Cooldown period active', value: triggers.cooldown },
    { label: 'Customer opted out', value: triggers.do_not_contact },
    { label: 'Attempt limit reached', value: triggers.max_attempts },
    { label: 'Permanent decline', value: triggers.hard_decline },
    ...Object.entries(triggers.other ?? {}).map(([key, value]) => ({
      label: stopReasonLabel(key),
      value,
    })),
  ];
  const triggerTotal = triggerRows.reduce((sum, row) => sum + row.value, 0);

  return (
    <div className="space-y-5">
      {/* Run complete.
          Deliberately factual, never celebratory: a low recovery rate is a real
          result, and BUILD_SPEC Section 11 is explicit that 100% resolution
          would be a red flag rather than a win. This block answers "what
          happened in THIS run" — the dashboard KPIs answer "what is in the
          ledger overall", and conflating the two is what makes repeat runs look
          like the system is inventing money. */}
      <Card className="border-accent/25 bg-accent/[0.03]">
        <div className="px-5 py-4">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10 ring-1 ring-accent/20">
              <CheckCircle2 className="h-4 w-4 text-accent" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-ink">
                {label ?? 'Recovery run complete'}
              </p>
              <p className="mt-0.5 text-xs text-ink-muted">
                {openedFromHistory ? 'Saved recovery run' : 'Recovery run complete'} ·{' '}
                {gatewayLabel(result.gateway)}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {runId ? (
                <Button asChild variant="secondary" size="sm">
                  <Link href={`/batch?run=${encodeURIComponent(runId)}`}>
                    <Clock className="h-3.5 w-3.5" aria-hidden="true" />
                    View run details
                  </Link>
                </Button>
              ) : null}
              <Button asChild variant="ghost" size="sm">
                <Link href="/events">
                  See the cases
                  <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                </Link>
              </Button>
            </div>
          </div>

          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-line pt-3.5 sm:grid-cols-4">
            <Stat label="Cases received" value={formatCount(result.total_records)} />
            <Stat label="Cases reviewed" value={formatCount(result.processed)} />
            <Stat
              label="Could not be read"
              value={formatCount(result.isolated_failures)}
              tone={result.isolated_failures > 0 ? 'warn' : undefined}
              hint="Incomplete records. Each was set aside so the rest of the run continued."
            />
            <Stat
              label="Already seen"
              value={formatCount(result.skipped_duplicates)}
              hint="Repeat cases. Revora never contacts a customer twice for the same thing."
            />
          </dl>

          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-line pt-3.5 sm:grid-cols-5">
            <Stat label="Revenue at risk" value={formatInr(money.amount_at_risk)} />
            <Stat
              label="Revenue recovered"
              value={formatInr(money.amount_recovered)}
              tone="good"
            />
            <Stat label="Recovery rate" value={formatPercent(result.recovery_rate)} />
            <Stat label="In progress" value={formatInr(money.amount_pending)} />
            <Stat label="Written off" value={formatInr(money.amount_lost)} />
          </dl>

          <p className="mt-3 border-t border-line pt-3 text-xs leading-relaxed text-ink-subtle">
            These figures describe this run only. Your Overview covers every case Revora
            has worked, so it grows as you run more analyses — each run works a fresh set
            of cases rather than re-counting earlier ones.
          </p>
        </div>
      </Card>

      {/* KPI row — same visual language as the dashboard. */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="Amount at risk"
          value={formatInr(money.amount_at_risk)}
          exactValue={formatInrExact(money.amount_at_risk)}
          context={`across ${formatCount(result.processed)} events`}
          icon={BadgeIndianRupee}
          tone="neutral"
        />
        <KpiCard
          label="Recovered"
          value={formatInr(money.amount_recovered)}
          exactValue={formatInrExact(money.amount_recovered)}
          context={`${formatInr(money.amount_attempted)} attempted`}
          icon={TrendingUp}
          tone="recovered"
        />
        <KpiCard
          label="Recovery rate"
          value={formatPercent(result.recovery_rate)}
          context="of amount at risk, from ledger state"
          icon={Target}
          tone="accent"
          help="A 100% rate would be a red flag, not a win — the spec is explicit about that."
        />
        <KpiCard
          label="Lost"
          value={formatInr(money.amount_lost)}
          exactValue={formatInrExact(money.amount_lost)}
          context={`${formatInr(money.amount_pending)} still pending`}
          icon={AlertTriangle}
          tone="unrecoverable"
        />
      </div>

      {/* Volume + stopping rules */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recovery activity</CardTitle>
            <CardDescription>What Revora did across this run.</CardDescription>
          </CardHeader>
          <div className="px-5 pb-5">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3.5">
              <Stat label="Records requested" value={formatCount(result.total_records)} />
              <Stat label="Processed" value={formatCount(result.processed)} />
              <Stat
                label="Isolated failures"
                value={formatCount(result.isolated_failures)}
                tone={result.isolated_failures > 0 ? 'warn' : undefined}
                hint="Records that failed without taking the batch down."
              />
              <Stat
                label="Duplicates skipped"
                value={formatCount(result.skipped_duplicates)}
                hint="Replayed records the generator injects deliberately."
              />
              <Stat
                label="Escalation limit reached"
                value={formatCount(result.escalation_ceiling_hits)}
                hint="Cases Revora would not push any further."
              />
              <Stat
                label="Needs review"
                value={formatCount(result.exceptions_raised)}
                hint="Cases Revora could not resolve confidently on its own."
              />
              <Stat
                label="Recovery attempts"
                value={formatCount(
                  Object.entries(result.action_breakdown)
                    .filter(([action]) => action !== 'no_action')
                    .reduce((sum, [, count]) => sum + count, 0),
                )}
                hint="Cases where Revora took a recovery action."
              />
              <Stat
                label="Cases recovered"
                value={formatCount(result.status_breakdown.recovered ?? 0)}
                tone="good"
              />
              <Stat
                label="Sent for human review"
                value={formatCount(result.status_breakdown.escalated ?? 0)}
                hint="Revora handed these to a person instead of acting automatically."
              />
            </dl>

            {/* Promises: reported only if the tracker actually produced any. */}
            <div className="mt-4 border-t border-line pt-3">
              {result.promises_made > 0 ? (
                <dl className="grid grid-cols-3 gap-4">
                  <Stat label="Promises made" value={formatCount(result.promises_made)} />
                  <Stat label="Kept" value={formatCount(result.promises_kept)} />
                  <Stat label="Broken" value={formatCount(result.promises_broken)} />
                </dl>
              ) : (
                <p className="text-xs leading-relaxed text-ink-subtle">
                  <span className="font-medium text-ink-muted">Promises to pay:</span>{' '}
                  none were recorded on this run.
                </p>
              )}
            </div>
          </div>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Where Revora stopped</CardTitle>
            <CardDescription>
              Cases it deliberately left alone, to stay within your limits.
            </CardDescription>
          </CardHeader>
          <div className="px-5 pb-5">
            {triggerTotal === 0 ? (
              <p className="text-xs leading-relaxed text-ink-subtle">
                Revora stayed within every limit on this run.
              </p>
            ) : (
              <ul className="space-y-2.5">
                {triggerRows.map((row) => {
                  const share = triggerTotal > 0 ? row.value / triggerTotal : 0;
                  return (
                    <li key={row.label}>
                      <div className="flex items-center justify-between gap-3">
                        <span
                          className={cn(
                            'text-xs',
                            row.value > 0 ? 'text-ink' : 'text-ink-subtle',
                          )}
                        >
                          {row.label}
                        </span>
                        <span
                          className={cn(
                            'tabular text-xs font-semibold',
                            row.value > 0 ? 'text-ink' : 'text-ink-subtle',
                          )}
                        >
                          {formatCount(row.value)}
                        </span>
                      </div>
                      <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-line/70">
                        <div
                          className="h-full rounded-full bg-stopped transition-all duration-500"
                          // eslint-disable-next-line react/forbid-dom-props
                          {...{ style: { width: `${share * 100}%` } }}
                        />
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </Card>
      </div>

      {/* Direction breakdown — reuses the dashboard component unchanged. */}
      <div className="h-[380px]">
        <DirectionBreakdown result={result} />
      </div>

    </div>
  );
}

function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: 'warn' | 'good';
}) {
  const content = (
    <div className="min-w-0">
      <dt className="flex items-center gap-1 text-micro uppercase text-ink-subtle">
        {label}
        {hint ? (
          <span
            aria-hidden="true"
            className="flex h-3 w-3 items-center justify-center rounded-full border border-line text-[8px]"
          >
            i
          </span>
        ) : null}
      </dt>
      <dd
        className={cn(
          'tabular mt-0.5 text-sm font-semibold',
          tone === 'warn' ? 'text-pending' : tone === 'good' ? 'text-recovered' : 'text-ink',
        )}
      >
        {value}
      </dd>
    </div>
  );

  if (!hint) return content;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div tabIndex={0} className="rounded outline-none focus-visible:ring-2 focus-visible:ring-accent">
          {content}
        </div>
      </TooltipTrigger>
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  );
}
