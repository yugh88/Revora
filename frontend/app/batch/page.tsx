'use client';

import * as React from 'react';
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
import { SiteHeader } from '../../components/ui/site-header';
import { Tooltip, TooltipContent, TooltipTrigger } from '../../components/ui/tooltip';
import { cn } from '../../components/ui/utils';
import {
  api,
  ApiError,
  formatCount,
  formatInr,
  formatInrExact,
  formatPercent,
  humanizeKey,
} from '../../lib/api-client';
import type { BatchResponse, GatewayUsed } from '../../lib/types';

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

export default function BatchPage() {
  const [size, setSize] = React.useState<RunSize>(50);
  const [gateway, setGateway] = React.useState<GatewayUsed>('local_simulation');
  const [running, setRunning] = React.useState(false);
  const [result, setResult] = React.useState<BatchResponse | null>(null);
  const [error, setError] = React.useState<ApiError | null>(null);
  const [elapsed, setElapsed] = React.useState(0);

  // A live counter during the run. A 500-record batch takes ~25s and a static
  // spinner for that long is indistinguishable from a hang.
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
      setResult(await api.runBatch({ count: size, gateway }));
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
    <div className="min-h-screen">
      <SiteHeader />

      <main className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">
            Recovery analysis
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
            Push a batch of synthetic revenue-risk records through the full engine and
            measure what actually came back.
          </p>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-5 lg:grid-cols-3">
          {/* ---------------- Configuration ---------------- */}
          <div className="animate-fade-up stagger-1 lg:col-span-1">
            <Card className="sticky top-24">
              <CardHeader>
                <CardTitle>Configuration</CardTitle>
                <CardDescription>
                  Choose the volume and which gateway executes.
                </CardDescription>
              </CardHeader>

              <div className="space-y-5 px-5 pb-5">
                <fieldset disabled={running}>
                  <legend className="text-micro font-semibold uppercase text-ink-subtle">
                    Records
                  </legend>
                  <div
                    role="radiogroup"
                    aria-label="Records per analysis"
                    className="mt-2.5 flex gap-2"
                  >
                    {SIZES.map((option) => (
                      <button
                        key={option}
                        type="button"
                        role="radio"
                        aria-checked={option === size}
                        disabled={running}
                        onClick={() => setSize(option)}
                        className={cn(
                          'tabular flex-1 rounded-lg border px-3 py-2.5 text-sm font-semibold transition-all',
                          'outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg',
                          'disabled:cursor-not-allowed disabled:opacity-60',
                          option === size
                            ? 'border-accent/50 bg-accent/[0.06] text-ink shadow-card'
                            : 'border-line bg-surface text-ink-subtle hover:border-line-strong hover:text-ink',
                        )}
                      >
                        {option}
                        <span className="mt-0.5 block text-micro font-normal text-ink-subtle">
                          {option === 50 ? 'quick pass' : 'full run'}
                        </span>
                      </button>
                    ))}
                  </div>
                </fieldset>

                <GatewayToggle value={gateway} onChange={setGateway} disabled={running} />

                {/* What pressing the button will actually do. */}
                <div className="rounded-lg border border-line bg-surface-raised/50 px-3 py-2.5">
                  <p className="flex items-center gap-1.5 text-micro font-semibold uppercase text-ink-subtle">
                    <Database className="h-3 w-3" aria-hidden="true" />
                    This writes state
                  </p>
                  <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
                    {size} synthetic records will be detected, diagnosed, scored, gated by
                    policy and executed via the{' '}
                    <span className="font-medium text-ink">
                      {gateway === 'local_simulation'
                        ? 'built-in simulator'
                        : 'Razorpay test sandbox'}
                    </span>
                    . Events, decisions, ledger rows and audit entries are all persisted —
                    they will appear in the events feed afterwards.
                  </p>
                </div>

                <Button
                  onClick={() => void run()}
                  disabled={running}
                  className="w-full"
                  aria-label={`Run recovery analysis on ${size} records using ${gateway}`}
                >
                  {running ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                      Running…
                    </>
                  ) : (
                    <>
                      <Play className="h-4 w-4" aria-hidden="true" />
                      Run recovery analysis
                    </>
                  )}
                </Button>
              </div>
            </Card>
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
              <Results result={result} />
            ) : (
              <IdleState />
            )}
          </div>
        </div>
      </main>
    </div>
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
  const phase =
    elapsed < 1.5
      ? 'Generating synthetic records and detecting risk'
      : elapsed < 4
        ? 'Diagnosing root causes and scoring interventions'
        : elapsed < 12
          ? 'Applying policy gates and executing permitted actions'
          : 'Verifying outcomes and writing the recovery ledger';

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
        Processing {formatCount(size)} records
      </h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-ink-muted">{phase}…</p>
      <p className="tabular mt-4 text-micro uppercase text-ink-subtle">
        {elapsed.toFixed(1)}s elapsed ·{' '}
        {gateway === 'local_simulation' ? 'built-in simulator' : 'Razorpay test sandbox'}
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

function Results({ result }: { result: BatchResponse }) {
  const money = result.money;
  const triggers = result.stopping_rule_triggers;
  const triggerRows = [
    { label: 'Cooldown', value: triggers.cooldown },
    { label: 'Do not contact', value: triggers.do_not_contact },
    { label: 'Max attempts', value: triggers.max_attempts },
    { label: 'Hard decline', value: triggers.hard_decline },
    ...Object.entries(triggers.other ?? {}).map(([key, value]) => ({
      label: humanizeKey(key),
      value,
    })),
  ];
  const triggerTotal = triggerRows.reduce((sum, row) => sum + row.value, 0);

  return (
    <div className="space-y-5">
      {/* Run complete — deliberately factual, not celebratory. */}
      <Card className="border-accent/25 bg-accent/[0.03]">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10 ring-1 ring-accent/20">
            <CheckCircle2 className="h-4.5 w-4.5 text-accent" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-ink">Run complete</p>
            <p className="tabular mt-0.5 text-xs text-ink-muted">
              {formatCount(result.processed)} of {formatCount(result.total_records)}{' '}
              records processed in {result.duration_seconds.toFixed(1)}s via{' '}
              {humanizeKey(result.gateway)}
            </p>
          </div>
          <Button asChild variant="secondary" size="sm">
            <Link href="/events">
              Inspect events
              <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            </Link>
          </Button>
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
            <CardTitle>Processing</CardTitle>
            <CardDescription>
              How the batch handled what the generator threw at it.
            </CardDescription>
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
                label="Escalation ceiling hits"
                value={formatCount(result.escalation_ceiling_hits)}
                hint="Events the engine would not escalate further."
              />
              <Stat label="Exceptions raised" value={formatCount(result.exceptions_raised)} />
              <Stat
                label="ML agreement"
                value={
                  result.ml_agreement_rate === null
                    ? 'No opinion'
                    : `${formatPercent(result.ml_agreement_rate)} (${formatCount(result.ml_agreements)}/${formatCount(result.ml_predictions)})`
                }
                hint="An absent classifier opinion is not counted as a disagreement."
              />
              <Stat label="Audit entries" value={formatCount(result.audit_entries)} />
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
                  <span className="font-medium text-ink-muted">
                    Promise-to-pay tracking:
                  </span>{' '}
                  no promises were recorded in this run. The promise watcher is not part
                  of the pipeline yet, so this is a real zero rather than a measurement.
                </p>
              )}
            </div>
          </div>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Stopping rules</CardTitle>
            <CardDescription>
              Where the engine deliberately declined to act. {formatCount(triggerTotal)}{' '}
              {triggerTotal === 1 ? 'trigger' : 'triggers'} in total.
            </CardDescription>
          </CardHeader>
          <div className="px-5 pb-5">
            {triggerTotal === 0 ? (
              <p className="text-xs leading-relaxed text-ink-subtle">
                No stopping rule fired in this run.
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

      <p className="text-micro text-ink-subtle">
        Batch {result.batch_id} · correlation {result.correlation_id} · seed{' '}
        {result.seed}
      </p>
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
  tone?: 'warn';
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
          tone === 'warn' ? 'text-pending' : 'text-ink',
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
