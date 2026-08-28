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
  formatInr,
  formatInrExact,
  formatPercent,
} from '../lib/api-client';
import {
  actionLabel,
  causeExplanation,
  caseTitle,
  channelLabel,
  decisionExplanation,
  policyReason,
  eventTypeLabel,
  gatewayLabel,
  outcomeLabel,
  rootCauseLabel,
  stopReasonLabel,
  toSentence,
} from '../lib/labels';
import type { EventDetailResponse } from '../lib/types';
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

  const recoveredAmount = detail.outcome?.amount_recovered ?? '0';
  const didRecover =
    detail.outcome?.resolved === 'recovered' && Number.parseFloat(recoveredAmount) > 0;

  return (
    <div className="space-y-5">
      {/* ---------------- The money, first ----------------
          A reader's first question is "how much, and did we get it back?" —
          not "what is the correlation id". Everything below answers "how", and
          it only earns attention once "what happened" is settled. */}
      <Card
        className={cn(
          'overflow-hidden',
          didRecover ? 'border-recovered/30' : 'border-line',
        )}
      >
        <div className="flex flex-wrap items-end justify-between gap-6 p-6">
          <div className="min-w-0">
            <p className="text-micro font-semibold uppercase tracking-wide text-ink-subtle">
              {caseTitle(event.type, event.customer_name)}
            </p>
            <p
              className={cn(
                'tabular mt-1.5 text-[2rem] font-semibold leading-none tracking-tight',
                didRecover ? 'text-recovered' : 'text-ink',
              )}
              title={formatInrExact(didRecover ? recoveredAmount : event.amount)}
            >
              {formatInr(didRecover ? recoveredAmount : event.amount)}
            </p>
            <p className="mt-2 text-sm text-ink-muted">
              {didRecover ? (
                <>
                  Recovered of{' '}
                  <span className="tabular font-medium text-ink">
                    {formatInr(event.amount)}
                  </span>{' '}
                  at risk
                </>
              ) : (
                <>
                  At risk ·{' '}
                  {detail.outcome ? outcomeLabel(detail.outcome.resolved) : 'Still being worked'}
                </>
              )}
            </p>
          </div>

          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <StatusBadge status={event.status} />
            {stopping?.hard_stop_reason ? (
              <Badge variant="stopped">
                {stopReasonLabel(stopping.hard_stop_reason)}
              </Badge>
            ) : null}
          </div>
        </div>

        {/* What Revora did about it, in one line. */}
        {latestDecision ? (
          <div className="border-t border-line bg-surface-raised/40 px-6 py-3">
            <p className="text-xs leading-relaxed text-ink-muted">
              <span className="font-medium text-ink">
                {rootCauseLabel(diagnosis?.root_cause)}
              </span>
              {' → '}
              <span className="font-medium text-ink">
                {actionLabel(latestDecision.action_code)}
              </span>
              {' → '}
              <span
                className={cn(
                  'font-medium',
                  policy?.status === 'allowed' ? 'text-recovered' : 'text-stopped',
                )}
              >
                {policy?.status === 'allowed' ? 'Policy approved' : 'Policy blocked'}
              </span>
            </p>
          </div>
        ) : null}
      </Card>

      {/* ---------------- A. Identity ---------------- */}
      <SectionCard
        title="The case"
        description="Who it involves, and what it is worth."
        icon={Info}
      >
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3.5 sm:grid-cols-3 lg:grid-cols-4">
          <Field label="Customer">{event.customer_name}</Field>
          <Field label="What happened">{eventTypeLabel(event.type)}</Field>
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
          <Field label="Detected">
            <time dateTime={event.detected_at}>{formatDateTime(event.detected_at)}</time>
          </Field>
          {detail.outcome ? (
            <>
              <Field label="Ledger outcome">{outcomeLabel(detail.outcome.resolved)}</Field>
              <Field label="Recovered" mono>
                {formatInrExact(detail.outcome.amount_recovered)}
              </Field>
            </>
          ) : null}
        </dl>
      </SectionCard>

      {/* ---------------- Why it happened ----------------
          No confidence percentage and no separate model block. A merchant
          cannot act on "87%", and the distinction between the rule engine and
          its second opinion is an internal safeguard, not a product concept.
          What they need is the cause, or an honest admission that Revora could
          not determine one and therefore did not act. */}
      <SectionCard
        title="Why it happened"
        description="What Revora determined was behind this."
        icon={Stethoscope}
        accent="authoritative"
      >
        {diagnosis ? (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-ink">
                {rootCauseLabel(diagnosis.root_cause)}
              </span>
              {diagnosis.is_low_confidence ? (
                <Badge variant="pending">Needs human review</Badge>
              ) : null}
            </div>

            {diagnosis.is_low_confidence ? (
              <p className="rounded-lg border border-pending/25 bg-pending/5 px-3 py-2 text-xs leading-relaxed text-ink-muted">
                Revora could not confidently determine the cause, so it did not take an
                automatic financial action. A person should review this case.
              </p>
            ) : null}

            <p className="text-xs leading-relaxed text-ink-muted">
              {causeExplanation(event.type, diagnosis.root_cause, diagnosis.is_low_confidence)}
            </p>
          </div>
        ) : (
          <Absent reason="Revora has not yet worked out why this happened." />
        )}
      </SectionCard>

      {/* ---------------- C. Decision ---------------- */}
      <SectionCard
        title="What Revora decided"
        description="The action it chose, and why."
        icon={Scale}
        accent="authoritative"
      >
        {latestDecision ? (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3.5 sm:grid-cols-3">
              <Field label="Recovery action">
                <span className="font-medium">{actionLabel(latestDecision.action_code)}</span>
              </Field>
              <Field label="Policy outcome">
                {policy?.status === 'allowed' ? 'Policy approved' : 'Policy blocked this action'}
              </Field>
            </dl>

            <div className="rounded-lg border border-line bg-surface-raised/50 px-3 py-2.5">
              <p className="text-micro uppercase text-ink-subtle">Why</p>
              <p className="mt-1 text-xs leading-relaxed text-ink-muted">
                {decisionExplanation(
                  latestDecision.action_code,
                  policy?.status,
                  policy?.rule_triggered,
                )}
              </p>
            </div>

            {decisions.length > 1 ? (
              <p className="text-micro text-ink-subtle">
                {decisions.length} decisions on this case; the most recent is shown.
              </p>
            ) : null}

          </div>
        ) : (
          <Absent reason="Revora has not yet decided what to do about this." />
        )}
      </SectionCard>

      {/* ---------------- Policy gate ---------------- */}
      <SectionCard
        title="Your policy"
        description="The limits Revora must work within."
        icon={ShieldCheck}
        accent="authoritative"
      >
        {policy ? (
          <div className="space-y-3">
            <Badge variant={policy.status === 'allowed' ? 'recovered' : 'stopped'}>
              {policy.status === 'allowed' ? 'Policy approved' : 'Policy blocked this action'}
            </Badge>

            {policy.status === 'blocked' ? (
              <p className="text-xs leading-relaxed text-ink-muted">
                {policyReason(policy.rule_triggered)
                  ? `Revora did not contact this customer because ${policyReason(policy.rule_triggered)}.`
                  : 'Revora did not act on this case because your policy did not allow it.'}
              </p>
            ) : (
              <p className="text-xs leading-relaxed text-ink-muted">
                This recovery action was within every limit you have set.
              </p>
            )}
          </div>
        ) : (
          <Absent reason="No policy evaluation has been recorded for this event." />
        )}
      </SectionCard>

      {/* ---------------- D. Stopping rules ---------------- */}
      <SectionCard
        title="How far Revora will go"
        description="The limits applied to this case."
        icon={Timer}
      >
        {stopping ? (
          <div className="space-y-3">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3.5 sm:grid-cols-3 lg:grid-cols-5">
              <Field label="Attempts used" mono>
                {stopping.attempts_used} of {stopping.max_attempts_for_type}
              </Field>
              <Field label="Next contact after">
                {stopping.cooldown_until ? (
                  <time dateTime={stopping.cooldown_until}>
                    {formatDateTime(stopping.cooldown_until)}
                  </time>
                ) : (
                  '—'
                )}
              </Field>
              <Field label="Customer opted out">
                {stopping.do_not_contact_snapshot ? (
                  <span className="inline-flex items-center gap-1 text-unrecoverable">
                    <Ban className="h-3 w-3" aria-hidden="true" />
                    Yes
                  </span>
                ) : (
                  'No'
                )}
              </Field>
              <Field label="Stopped because">
                {stopping.hard_stop_reason ? stopReasonLabel(stopping.hard_stop_reason) : '—'}
              </Field>
            </dl>

            {stopping.hard_stop_reason ? (
              <p className="rounded-lg border border-stopped/25 bg-stopped/5 px-3 py-2 text-xs leading-relaxed text-ink-muted">
                Revora will take no further action on this case:{' '}
                <span className="font-medium text-ink">
                  {stopReasonLabel(stopping.hard_stop_reason)}
                </span>
                .
              </p>
            ) : null}
          </div>
        ) : (
          <Absent reason="Revora has not started working this case yet." />
        )}
      </SectionCard>

      {/* ---------------- Attempts ---------------- */}
      {detail.attempts.length > 0 ? (
        <SectionCard
          title="What Revora did"
          description="Each recovery attempt on this case."
          icon={Gauge}
        >
          <ul className="space-y-2">
            {detail.attempts.map((attempt) => (
              <li
                key={attempt.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line bg-surface-raised/50 px-3 py-2.5"
              >
                <span className="text-xs font-medium text-ink">
                  Attempt {attempt.attempt_number}
                </span>
                <span className="text-xs text-ink-muted">{toSentence(attempt.status)}</span>
                <time
                  dateTime={attempt.initiated_at}
                  className="ml-auto text-micro text-ink-subtle"
                >
                  {formatDateTime(attempt.initiated_at)}
                </time>
              </li>
            ))}
          </ul>
        </SectionCard>
      ) : null}
    </div>
  );
}
