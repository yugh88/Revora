'use client';

import * as React from 'react';
import {
  Ban,
  Brain,
  Gauge,
  Info,
  Scale,
  ShieldCheck,
  Stethoscope,
  Timer,
} from 'lucide-react';

import {
  formatDateTime,
  formatInrExact,
  formatPercent,
  humanizeKey,
} from '../lib/api-client';
import { EVENT_TYPE_LABELS, type EventDetailResponse } from '../lib/types';
import { StatusBadge, STATUS_HINT } from './StatusBadge';
import { Badge } from './ui/badge';
import { Card, CardDescription, CardHeader, CardTitle } from './ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { cn } from './ui/utils';

/**
 * Everything the engine recorded about one event.
 *
 * The single most important thing this component communicates is the boundary
 * Section 4a draws: the RULE ENGINE is authoritative for the action actually
 * taken, and the CLASSIFIER is an independent second opinion that never
 * overrides it. So the two verdicts are rendered as visually distinct blocks
 * with explicit labels, and the ML block states in words that it did not decide
 * anything. Putting them side by side as equals would misrepresent the
 * architecture.
 *
 * No field is invented. Sections whose data the backend did not return are
 * shown as explicitly absent, with the reason, rather than as zeros.
 */

function Field({
  label,
  children,
  mono,
}: {
  label: string;
  children: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-micro uppercase text-ink-subtle">{label}</dt>
      <dd
        className={cn(
          'mt-0.5 truncate text-xs text-ink',
          mono && 'tabular font-medium',
        )}
      >
        {children}
      </dd>
    </div>
  );
}

function SectionCard({
  title,
  description,
  icon: Icon,
  children,
  accent,
}: {
  title: string;
  description?: string;
  icon: typeof Info;
  children: React.ReactNode;
  accent?: 'authoritative' | 'advisory';
}) {
  return (
    <Card
      className={cn(
        accent === 'authoritative' && 'border-accent/25',
        accent === 'advisory' && 'border-dashed',
      )}
    >
      <CardHeader>
        <div className="flex items-start gap-2.5">
          <span
            className={cn(
              'mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg',
              accent === 'authoritative'
                ? 'bg-accent/10 text-accent ring-1 ring-accent/20'
                : 'bg-surface-raised text-ink-muted ring-1 ring-line',
            )}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <CardTitle>{title}</CardTitle>
            {description ? <CardDescription>{description}</CardDescription> : null}
          </div>
        </div>
      </CardHeader>
      <div className="px-5 pb-5">{children}</div>
    </Card>
  );
}

function Absent({ reason }: { reason: string }) {
  return <p className="text-xs leading-relaxed text-ink-subtle">{reason}</p>;
}

export function EventDrilldown({ detail }: { detail: EventDetailResponse }) {
  const { event, diagnosis, ml, decisions, stopping_rule_state: stopping } = detail;
  const latestDecision = decisions.length ? decisions[decisions.length - 1] : null;
  const policy = latestDecision?.policy_result ?? null;

  return (
    <div className="space-y-5">
      {/* ---------------- A. Identity ---------------- */}
      <SectionCard
        title="Event"
        description="What was detected, and what it is worth."
        icon={Info}
      >
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3.5 sm:grid-cols-3 lg:grid-cols-4">
          <Field label="Event ID" mono>
            {event.id}
          </Field>
          <Field label="Type">
            {EVENT_TYPE_LABELS[event.type] ?? humanizeKey(event.type)}
          </Field>
          <Field label="Amount" mono>
            {formatInrExact(event.amount)}
          </Field>
          <Field label="Status">
            <Tooltip>
              <TooltipTrigger asChild>
                <span tabIndex={0} className="inline-block outline-none">
                  <StatusBadge status={event.status} />
                </span>
              </TooltipTrigger>
              <TooltipContent>{STATUS_HINT[event.status]}</TooltipContent>
            </Tooltip>
          </Field>
          <Field label="Customer" mono>
            {event.customer_id}
          </Field>
          <Field label="Merchant" mono>
            {event.merchant_id}
          </Field>
          <Field label="Gateway">{humanizeKey(event.gateway_used)}</Field>
          <Field label="Source reference" mono>
            {event.source_ref ?? '—'}
          </Field>
          <Field label="Detected">
            <time dateTime={event.detected_at}>{formatDateTime(event.detected_at)}</time>
          </Field>
          <Field label="Correlation ID" mono>
            <span title={event.correlation_id}>{event.correlation_id}</span>
          </Field>
          {detail.outcome ? (
            <>
              <Field label="Ledger outcome">{humanizeKey(detail.outcome.resolved)}</Field>
              <Field label="Recovered" mono>
                {formatInrExact(detail.outcome.amount_recovered)}
              </Field>
            </>
          ) : null}
        </dl>
      </SectionCard>

      {/* ---------------- B. Diagnosis (rule) + ML ---------------- */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <SectionCard
          title="Rule diagnosis"
          description="Deterministic. This is what the engine acted on."
          icon={Stethoscope}
          accent="authoritative"
        >
          {diagnosis ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="accent">Authoritative</Badge>
                <span className="text-sm font-semibold text-ink">
                  {humanizeKey(diagnosis.root_cause)}
                </span>
                <span
                  className={cn(
                    'tabular rounded-md px-1.5 py-0.5 text-micro font-medium',
                    diagnosis.is_low_confidence
                      ? 'bg-pending/10 text-pending'
                      : 'bg-recovered/10 text-recovered',
                  )}
                >
                  {formatPercent(diagnosis.confidence, 0)} confidence
                </span>
              </div>

              {diagnosis.is_low_confidence ? (
                <p className="rounded-lg border border-pending/25 bg-pending/5 px-3 py-2 text-xs leading-relaxed text-ink-muted">
                  Below the engine&rsquo;s confidence threshold. This event is routed for
                  human review rather than acted on with false certainty.
                </p>
              ) : null}

              <div>
                <p className="text-micro uppercase text-ink-subtle">Evidence</p>
                <ul className="mt-1.5 space-y-1">
                  {diagnosis.evidence.map((item, index) => (
                    <li
                      key={index}
                      className="rounded-md bg-surface-raised/70 px-2 py-1 text-xs text-ink-muted"
                    >
                      {String(item)}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : (
            <Absent reason="The pipeline has not diagnosed this event yet." />
          )}
        </SectionCard>

        <SectionCard
          title="ML second opinion"
          description="An independent check. It did not decide anything."
          icon={Brain}
          accent="advisory"
        >
          {ml ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="neutral">Advisory only</Badge>
                <span className="text-sm font-semibold text-ink">
                  {humanizeKey(ml.predicted_root_cause)}
                </span>
                <span className="tabular rounded-md bg-surface-raised px-1.5 py-0.5 text-micro font-medium text-ink-muted">
                  {formatPercent(ml.confidence, 0)} confidence
                </span>
              </div>

              <div
                className={cn(
                  'rounded-lg border px-3 py-2',
                  ml.agrees_with_rule_engine
                    ? 'border-recovered/25 bg-recovered/5'
                    : 'border-pending/25 bg-pending/5',
                )}
              >
                <p className="text-xs font-medium text-ink">
                  {ml.agrees_with_rule_engine
                    ? 'Agrees with the rule engine'
                    : 'Disagrees with the rule engine — needs review'}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-ink-muted">
                  {ml.agrees_with_rule_engine
                    ? 'Both reached the same root cause with sufficient confidence. The action still came from the rule engine.'
                    : 'The classifier reached a different conclusion, or was not confident enough to corroborate. The rule engine remained authoritative and its diagnosis drove the action; this disagreement is surfaced so a human can review it.'}
                </p>
              </div>

              <dl className="grid grid-cols-2 gap-3">
                <Field label="Model version" mono>
                  {ml.model_version}
                </Field>
                <Field label="Predicted">
                  <time dateTime={ml.predicted_at}>{formatDateTime(ml.predicted_at)}</time>
                </Field>
              </dl>
            </div>
          ) : (
            <Absent reason="No classifier opinion was recorded for this event. An absent opinion is not a disagreement — the deterministic pipeline ran unaffected." />
          )}
        </SectionCard>
      </div>

      {/* ---------------- C. Decision ---------------- */}
      <SectionCard
        title="Decision"
        description="What the engine chose, scored and justified."
        icon={Scale}
        accent="authoritative"
      >
        {latestDecision ? (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3.5 sm:grid-cols-4">
              <Field label="Action">
                <span className="font-medium">{humanizeKey(latestDecision.action_code)}</span>
              </Field>
              <Field label="Recovery probability" mono>
                {formatPercent(latestDecision.recovery_probability, 1)}
              </Field>
              <Field label="Probability source">
                {humanizeKey(latestDecision.probability_source)}
              </Field>
              <Field label="Policy version" mono>
                v{latestDecision.policy_version}
              </Field>
            </dl>

            <div className="rounded-lg border border-line bg-surface-raised/50 px-3 py-2.5">
              <p className="text-micro uppercase text-ink-subtle">Reasoning</p>
              <p className="mt-1 text-xs leading-relaxed text-ink-muted">
                {latestDecision.reasoning_text}
              </p>
            </div>

            {decisions.length > 1 ? (
              <p className="text-micro text-ink-subtle">
                {decisions.length} decisions recorded for this event; the latest is shown.
              </p>
            ) : null}

            <details className="group">
              <summary className="cursor-pointer list-none rounded-lg px-2 py-1.5 text-xs font-medium text-ink-muted outline-none transition-colors hover:bg-surface-raised focus-visible:ring-2 focus-visible:ring-accent">
                <span className="inline-flex items-center gap-1.5">
                  <span className="transition-transform group-open:rotate-90" aria-hidden="true">
                    ▸
                  </span>
                  Decision factors
                </span>
              </summary>
              <pre className="mt-2 max-h-72 overflow-auto rounded-lg border border-line bg-surface-raised/60 p-3 text-micro leading-relaxed text-ink-muted">
                {JSON.stringify(latestDecision.decision_factors, null, 2)}
              </pre>
            </details>
          </div>
        ) : (
          <Absent reason="No decision has been recorded for this event." />
        )}
      </SectionCard>

      {/* ---------------- Policy gate ---------------- */}
      <SectionCard
        title="Policy gate"
        description="Merchant-configurable bounds. Authoritative over the score."
        icon={ShieldCheck}
        accent="authoritative"
      >
        {policy ? (
          <div className="space-y-3">
            <Badge variant={policy.status === 'allowed' ? 'recovered' : 'stopped'}>
              {policy.status === 'allowed' ? 'Action allowed' : 'Action blocked'}
            </Badge>

            {policy.status === 'blocked' ? (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3.5 sm:grid-cols-4">
                <Field label="Rule triggered">
                  {humanizeKey(String(policy.rule_triggered ?? '—'))}
                </Field>
                <Field label="Threshold checked" mono>
                  {String(policy.threshold_checked ?? '—')}
                </Field>
                <Field label="Actual value" mono>
                  {String(policy.actual_value ?? '—')}
                </Field>
                <Field label="Threshold" mono>
                  {String(policy.threshold_value ?? '—')}
                </Field>
              </dl>
            ) : (
              <p className="text-xs leading-relaxed text-ink-muted">
                Every rule passed, so the highest-scoring permitted action was taken.
              </p>
            )}
          </div>
        ) : (
          <Absent reason="No policy evaluation has been recorded for this event." />
        )}
      </SectionCard>

      {/* ---------------- D. Stopping rules ---------------- */}
      <SectionCard
        title="Stopping-rule state"
        description="How far the engine is allowed to go on this event."
        icon={Timer}
      >
        {stopping ? (
          <div className="space-y-3">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3.5 sm:grid-cols-3 lg:grid-cols-5">
              <Field label="Attempts used" mono>
                {stopping.attempts_used} / {stopping.max_attempts_for_type}
              </Field>
              <Field label="Escalation level" mono>
                L{stopping.escalation_level}
              </Field>
              <Field label="Cooldown until">
                {stopping.cooldown_until ? (
                  <time dateTime={stopping.cooldown_until}>
                    {formatDateTime(stopping.cooldown_until)}
                  </time>
                ) : (
                  '—'
                )}
              </Field>
              <Field label="Do not contact">
                {stopping.do_not_contact_snapshot ? (
                  <span className="inline-flex items-center gap-1 text-unrecoverable">
                    <Ban className="h-3 w-3" aria-hidden="true" />
                    Yes
                  </span>
                ) : (
                  'No'
                )}
              </Field>
              <Field label="Hard stop">
                {stopping.hard_stop_reason ? humanizeKey(stopping.hard_stop_reason) : '—'}
              </Field>
            </dl>

            {stopping.hard_stop_reason ? (
              <p className="rounded-lg border border-stopped/25 bg-stopped/5 px-3 py-2 text-xs leading-relaxed text-ink-muted">
                The engine will take no further automated action on this event:{' '}
                <span className="font-medium text-ink">
                  {humanizeKey(stopping.hard_stop_reason)}
                </span>
                .
              </p>
            ) : null}
          </div>
        ) : (
          <Absent reason="No stopping-rule state was recorded — the engine did not reach the point of bounding this event." />
        )}
      </SectionCard>

      {/* ---------------- Attempts ---------------- */}
      {detail.attempts.length > 0 ? (
        <SectionCard
          title="Execution attempts"
          description="Every attempt carries an idempotency key; none can run twice."
          icon={Gauge}
        >
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-line text-micro uppercase text-ink-subtle">
                  <th scope="col" className="py-2 pr-3">
                    #
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Status
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Gateway
                  </th>
                  <th scope="col" className="py-2 pr-3">
                    Provider ref
                  </th>
                  <th scope="col" className="py-2">
                    Failure reason
                  </th>
                </tr>
              </thead>
              <tbody>
                {detail.attempts.map((attempt) => (
                  <tr key={attempt.id} className="border-b border-line/60 last:border-0">
                    <td className="tabular py-2 pr-3 text-ink">{attempt.attempt_number}</td>
                    <td className="py-2 pr-3 text-ink-muted">
                      {humanizeKey(attempt.status)}
                    </td>
                    <td className="py-2 pr-3 text-ink-muted">
                      {humanizeKey(attempt.gateway_used)}
                    </td>
                    <td className="tabular py-2 pr-3 text-ink-subtle">
                      {attempt.provider_ref ?? '—'}
                    </td>
                    <td className="py-2 text-ink-subtle">{attempt.failure_reason ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      ) : null}
    </div>
  );
}
