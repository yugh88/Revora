'use client';

import * as React from 'react';
import Link from 'next/link';
import { AlertCircle, ArrowRight, FlaskConical, Loader2, Play } from 'lucide-react';

import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { AppShell } from '../../components/ui/site-header';
import { cn } from '../../components/ui/utils';
import { api, ApiError, formatInr } from '../../lib/api-client';
import {
  eventStatusLabel,
  eventTypeLabel,
  humanSentence,
  humanValue,
} from '../../lib/labels';
import { EVENT_TYPES, type DryRunResponse, type EventType } from '../../lib/types';

/**
 * Run Recovery — a testing console, not a contact tool.
 *
 * The point is to prove Revora genuinely works: describe one case, submit it,
 * and watch it travel through the SAME pipeline every real case uses. The trace
 * on the right is read back from what the engine actually recorded — the
 * diagnosis it stored, the action it chose, the policy verdict it returned, the
 * audit rows it wrote. Nothing is simulated in the browser.
 *
 * Every input changes something real. There is no field here that only affects
 * presentation: each one feeds diagnosis, scoring, the policy gate or a
 * stopping rule.
 */

/** The gateway codes the diagnosis engine actually recognises. */
const FAILURE_REASONS: Array<{ value: string; label: string }> = [
  { value: '', label: 'Not known' },
  { value: 'BAD_REQUEST_CARD_EXPIRED', label: 'Card expired' },
  { value: 'BAD_REQUEST_PAYMENT_INSUFFICIENT_FUNDS', label: 'Insufficient funds' },
  { value: 'GATEWAY_ERROR_ISSUER_DECLINED', label: 'Bank declined' },
  { value: 'GATEWAY_ERROR_ISSUER_DOWN', label: 'Bank unavailable' },
  { value: 'GATEWAY_ERROR_TIMEOUT', label: 'Payment timed out' },
  { value: 'BAD_REQUEST_PAYMENT_FAILED', label: 'Payment failed' },
  { value: 'BAD_REQUEST_MANDATE_REVOKED', label: 'Mandate revoked' },
  { value: 'BAD_REQUEST_MANDATE_NOT_AUTHENTICATED', label: 'Mandate not authenticated' },
  { value: 'BAD_REQUEST_SUBSCRIPTION_HALTED', label: 'Subscription halted' },
  { value: 'BAD_REQUEST_RISK_THRESHOLD_EXCEEDED', label: 'Blocked for risk' },
];

const STATUS_STYLE: Record<string, { dot: string; text: string }> = {
  passed: { dot: 'bg-recovered', text: 'text-recovered' },
  blocked: { dot: 'bg-unrecoverable', text: 'text-unrecoverable' },
  skipped: { dot: 'bg-line-strong', text: 'text-ink-subtle' },
  info: { dot: 'bg-pending', text: 'text-pending' },
};

const inputClass =
  'mt-1.5 h-9 w-full rounded-lg border border-line bg-surface px-3 text-xs text-ink outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30';

export default function RunRecoveryPage() {
  const [type, setType] = React.useState<EventType>('payment_degraded');
  const [name, setName] = React.useState('Meera Nair');
  const [amount, setAmount] = React.useState('8500.00');
  const [reason, setReason] = React.useState('BAD_REQUEST_CARD_EXPIRED');
  const [attempts, setAttempts] = React.useState(0);
  const [daysOverdue, setDaysOverdue] = React.useState(30);
  const [successRate, setSuccessRate] = React.useState(0.7);
  const [delayDays, setDelayDays] = React.useState(3);
  const [doNotContact, setDoNotContact] = React.useState(false);

  const [result, setResult] = React.useState<DryRunResponse | null>(null);
  const [running, setRunning] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      setResult(
        await api.dryRun({
          event_type: type,
          customer_name: name.trim() || 'Test Customer',
          amount,
          gateway_error_code: reason || null,
          attempts_already_made: attempts,
          days_overdue: type === 'invoice_overdue' ? daysOverdue : null,
          payment_success_rate: successRate,
          avg_payment_delay_days: delayDays,
          do_not_contact: doNotContact,
        }),
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.userMessage
          : 'That test case could not be run.',
      );
    } finally {
      setRunning(false);
    }
  };

  return (
    <AppShell>
      <main className="mx-auto max-w-[1240px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight text-ink">
              Run recovery
            </h1>
            <Badge variant="accent">Testing console</Badge>
          </div>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
            Describe one case and watch Revora work it. This runs the real pipeline —
            the same diagnosis, scoring, policy limits and communication rules every
            recovery uses.
          </p>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-5 lg:grid-cols-5">
          <div className="lg:col-span-2">
            <Card className="animate-fade-up">
              <CardHeader>
                <CardTitle>Describe the case</CardTitle>
                <CardDescription>
                  Every field below changes what Revora decides.
                </CardDescription>
              </CardHeader>

              <div className="space-y-3.5 px-5 pb-5">
                <Field label="What went wrong">
                  <select
                    value={type}
                    onChange={(event) => setType(event.target.value as EventType)}
                    className={inputClass}
                  >
                    {EVENT_TYPES.map((option) => (
                      <option key={option} value={option}>
                        {eventTypeLabel(option)}
                      </option>
                    ))}
                  </select>
                </Field>

                <div className="grid grid-cols-2 gap-3">
                  <Field label="Customer">
                    <input
                      type="text"
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                      className={inputClass}
                    />
                  </Field>
                  <Field label="Amount at risk">
                    <input
                      type="number"
                      inputMode="decimal"
                      step="0.01"
                      min="1"
                      value={amount}
                      onChange={(event) => setAmount(event.target.value)}
                      className={cn(inputClass, 'tabular')}
                    />
                  </Field>
                </div>

                <Field label="Why it failed" hint="drives the diagnosis">
                  <select
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    className={inputClass}
                  >
                    {FAILURE_REASONS.map((option) => (
                      <option key={option.value || 'none'} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </Field>

                <div className="grid grid-cols-2 gap-3">
                  <Field label="Attempts made" hint="stopping rules">
                    <input
                      type="number"
                      min="0"
                      max="10"
                      value={attempts}
                      onChange={(event) => setAttempts(Number(event.target.value))}
                      className={cn(inputClass, 'tabular')}
                    />
                  </Field>
                  {type === 'invoice_overdue' ? (
                    <Field label="Days overdue">
                      <input
                        type="number"
                        min="0"
                        max="365"
                        value={daysOverdue}
                        onChange={(event) => setDaysOverdue(Number(event.target.value))}
                        className={cn(inputClass, 'tabular')}
                      />
                    </Field>
                  ) : (
                    <Field label="Usually late by" hint="days">
                      <input
                        type="number"
                        min="0"
                        max="180"
                        value={delayDays}
                        onChange={(event) => setDelayDays(Number(event.target.value))}
                        className={cn(inputClass, 'tabular')}
                      />
                    </Field>
                  )}
                </div>

                <Field
                  label={`Pays successfully ${(successRate * 100).toFixed(0)}% of the time`}
                  hint="tilts the odds"
                >
                  <input
                    type="range"
                    min="0"
                    max="100"
                    value={successRate * 100}
                    onChange={(event) => setSuccessRate(Number(event.target.value) / 100)}
                    className="mt-2 w-full accent-[hsl(var(--accent))]"
                  />
                </Field>

                <label className="flex cursor-pointer items-center justify-between gap-3 rounded-lg border border-line px-3 py-2.5">
                  <span className="text-xs text-ink">
                    Customer has opted out of contact
                    <span className="mt-0.5 block text-micro text-ink-subtle">
                      An absolute stop — nothing may reach them
                    </span>
                  </span>
                  <input
                    type="checkbox"
                    checked={doNotContact}
                    onChange={(event) => setDoNotContact(event.target.checked)}
                    className="h-4 w-4 accent-[hsl(var(--accent))]"
                  />
                </label>

                <Button className="w-full" onClick={() => void run()} disabled={running}>
                  {running ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Play className="h-4 w-4" aria-hidden="true" />
                  )}
                  {running ? 'Running…' : 'Run this case'}
                </Button>

                <p className="text-xs leading-relaxed text-ink-subtle">
                  Safe demo environment. No real payment is taken and no customer is
                  contacted.
                </p>
              </div>
            </Card>
          </div>

          <div className="lg:col-span-3">
            {error ? (
              <Card className="animate-fade-up border-unrecoverable/25">
                <div className="flex gap-3 p-6">
                  <AlertCircle
                    className="h-5 w-5 shrink-0 text-unrecoverable"
                    aria-hidden="true"
                  />
                  <div>
                    <p className="text-sm font-semibold text-ink">
                      That case could not be run
                    </p>
                    <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">{error}</p>
                  </div>
                </div>
              </Card>
            ) : result ? (
              <Trace result={result} />
            ) : (
              <Card className="animate-fade-up flex flex-col items-center px-6 py-20 text-center">
                <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-line bg-surface-raised">
                  <FlaskConical className="h-5 w-5 text-ink-subtle" aria-hidden="true" />
                </span>
                <h2 className="mt-4 text-base font-semibold text-ink">Nothing run yet</h2>
                <p className="mt-2 max-w-md text-sm leading-relaxed text-ink-muted">
                  Describe a case and run it. Every step Revora takes appears here — what
                  it diagnosed, what it decided, whether your policy allowed it, and what
                  it recovered.
                </p>
              </Card>
            )}
          </div>
        </div>
      </main>
    </AppShell>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-micro uppercase text-ink-subtle">{label}</span>
      {hint ? <span className="ml-1.5 text-micro text-ink-subtle/70">· {hint}</span> : null}
      {children}
    </label>
  );
}

function Trace({ result }: { result: DryRunResponse }) {
  return (
    <Card className="animate-fade-up">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>What Revora did</CardTitle>
            <CardDescription>
              Read back from what the engine recorded — not a simulation of it.
            </CardDescription>
          </div>
          <div className="text-right">
            <p className="text-micro uppercase text-ink-subtle">Recovered</p>
            <p
              className={cn(
                'tabular text-xl font-semibold',
                Number.parseFloat(result.amount_recovered) > 0
                  ? 'text-recovered'
                  : 'text-ink',
              )}
            >
              {formatInr(result.amount_recovered)}
            </p>
          </div>
        </div>
      </CardHeader>

      <ol className="px-5 pb-5">
        {result.steps.map((step, index) => {
          const style = STATUS_STYLE[step.status] ?? STATUS_STYLE.info;
          const last = index === result.steps.length - 1;
          return (
            <li key={`${step.stage}-${index}`} className="flex gap-3">
              {/* A dot per step joined by a line, so the trace reads as one
                  continuous run rather than a list of unrelated facts. */}
              <div className="flex flex-col items-center">
                <span
                  className={cn('mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full', style.dot)}
                />
                {!last ? <span className="w-px flex-1 bg-line" /> : null}
              </div>

              <div className={cn('min-w-0 flex-1', last ? 'pb-0' : 'pb-4')}>
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-sm font-medium text-ink">{step.title}</span>
                  <span className={cn('text-xs font-medium', style.text)}>
                    {humanValue(step.outcome)}
                  </span>
                </div>
                {step.detail ? (
                  <p className="mt-1 text-xs leading-relaxed text-ink-muted">
                    {humanSentence(step.detail)}
                  </p>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-5 py-3.5">
        <p className="text-xs text-ink-muted">
          Final status:{' '}
          <span className="font-medium text-ink">
            {eventStatusLabel(result.final_status)}
          </span>{' '}
          · {result.audit_entries} audit entries
        </p>
        <Button asChild variant="ghost" size="sm">
          <Link href={`/events/${result.event_id}?from=batch`}>
            Open this case
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </Link>
        </Button>
      </div>
    </Card>
  );
}
