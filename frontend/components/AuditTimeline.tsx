'use client';

import * as React from 'react';
import {
  Check,
  CircleDashed,
  Gavel,
  PlayCircle,
  Radar,
  ShieldCheck,
  Stethoscope,
  TrendingUp,
  UserCog,
  type LucideIcon,
} from 'lucide-react';

import { formatDateTime, formatRelative } from '../lib/api-client';
import { auditActionLabel, stageLabel, stageMeaning } from '../lib/labels';
import { PIPELINE_STAGES, type AuditEntry, type PipelineStage } from '../lib/types';
import { Card, CardDescription, CardHeader, CardTitle } from './ui/card';
import { cn } from './ui/utils';

/**
 * The Section 2 pipeline for one event:
 *
 *   detection → diagnosis → decision → policy → execution → verification
 *   → recovery / escalation
 *
 * The important design decision here is how ABSENCE is rendered. A missing
 * stage is not a gap or an error — an event stopped at the policy gate is
 * SUPPOSED to have no execution entries, and that is the engine working. So
 * unreached stages are shown explicitly, greyed, with a sentence explaining why
 * the pipeline ended where it did, derived from the last stage actually
 * recorded. Nothing is fabricated: every entry rendered comes from the audit
 * log, and every stage marked unreached genuinely has no entry.
 */

const STAGE_ICON: Record<PipelineStage, LucideIcon> = {
  detection: Radar,
  diagnosis: Stethoscope,
  decision: Gavel,
  policy: ShieldCheck,
  execution: PlayCircle,
  verification: Check,
  recovery: TrendingUp,
  escalation: UserCog,
};

const STAGE_LABEL: Record<PipelineStage, string> = {
  detection: 'Detection',
  diagnosis: 'Diagnosis',
  decision: 'Decision',
  policy: 'Policy',
  execution: 'Execution',
  verification: 'Verification',
  recovery: 'Recovery',
  escalation: 'Escalation',
};

/**
 * Why the pipeline stopped where it did.
 *
 * recovery and escalation are alternatives — an event reaches one or the other,
 * never both — so their absence is described as "not this path" rather than
 * "not reached".
 */
function absenceReason(
  stage: PipelineStage,
  lastReached: PipelineStage | null,
  reachedRecovery: boolean,
  reachedEscalation: boolean,
): string {
  if (stage === 'recovery') {
    return reachedEscalation
      ? 'Not this path — the event was escalated to a human instead.'
      : 'The event has not been recovered.';
  }
  if (stage === 'escalation') {
    return reachedRecovery
      ? 'Not this path — the event was recovered without escalation.'
      : 'The event was not escalated to a human.';
  }
  if (lastReached === 'policy') {
    return 'Execution was not reached because the policy gate blocked every permitted action.';
  }
  if (lastReached === 'diagnosis') {
    return 'The pipeline did not proceed past diagnosis for this event.';
  }
  return `Not reached — the pipeline ended at ${STAGE_LABEL[lastReached ?? 'detection'].toLowerCase()}.`;
}

export function AuditTimeline({
  entries,
  stagesPresent,
  stagesMissing,
}: {
  entries: AuditEntry[];
  stagesPresent: string[];
  stagesMissing: string[];
}) {
  // Memoised: a fresh Set on every render would change the identity of the
  // useMemo dependency below and defeat the memo entirely.
  const present = React.useMemo(() => new Set(stagesPresent), [stagesPresent]);
  const missing = React.useMemo(() => new Set(stagesMissing), [stagesMissing]);

  const byStage = React.useMemo(() => {
    const grouped = new Map<string, AuditEntry[]>();
    for (const entry of entries) {
      const list = grouped.get(entry.stage) ?? [];
      list.push(entry);
      grouped.set(entry.stage, list);
    }
    return grouped;
  }, [entries]);

  const lastReached = React.useMemo(() => {
    let last: PipelineStage | null = null;
    for (const stage of PIPELINE_STAGES) {
      if (present.has(stage)) last = stage;
    }
    return last;
  }, [present]);

  const reachedRecovery = present.has('recovery');
  const reachedEscalation = present.has('escalation');

  return (
    <Card>
      <CardHeader>
        <CardTitle>What happened, step by step</CardTitle>
        <CardDescription>
          Every action Revora took on this case, in order and never edited.
        </CardDescription>
      </CardHeader>

      <div className="px-5 pb-5">
        <ol className="relative space-y-0">
          {PIPELINE_STAGES.map((stage, index) => {
            const stageEntries = byStage.get(stage) ?? [];
            const reached = present.has(stage);
            const unreached = missing.has(stage);
            const Icon = STAGE_ICON[stage];
            const isLast = index === PIPELINE_STAGES.length - 1;

            return (
              <li key={stage} className="relative flex gap-3 pb-5 last:pb-0">
                {/* Connector rail */}
                {!isLast ? (
                  <span
                    aria-hidden="true"
                    className={cn(
                      'absolute left-[13px] top-7 h-[calc(100%-1.75rem)] w-px',
                      reached ? 'bg-accent/30' : 'bg-line',
                    )}
                  />
                ) : null}

                <span
                  className={cn(
                    'relative z-10 flex h-[27px] w-[27px] shrink-0 items-center justify-center rounded-full border transition-colors',
                    reached
                      ? 'border-accent/30 bg-accent/10 text-accent'
                      : 'border-line bg-surface text-ink-subtle/60',
                  )}
                >
                  {reached ? (
                    <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                  ) : (
                    <CircleDashed className="h-3.5 w-3.5" aria-hidden="true" />
                  )}
                </span>

                <div className="min-w-0 flex-1 pt-0.5">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <h3
                      className={cn(
                        'text-xs font-semibold uppercase tracking-wide',
                        reached ? 'text-ink' : 'text-ink-subtle',
                      )}
                    >
                      {STAGE_LABEL[stage]}
                    </h3>
                    {reached ? (
                      <span className="tabular text-micro text-ink-subtle">
                        {stageEntries.length}{' '}
                        {stageEntries.length === 1 ? 'step' : 'steps'}
                      </span>
                    ) : null}
                  </div>

                  {unreached ? (
                    <p className="mt-1 text-xs leading-relaxed text-ink-subtle">
                      {absenceReason(stage, lastReached, reachedRecovery, reachedEscalation)}
                    </p>
                  ) : (
                    <>
                      {/* The stage's meaning in plain language. The engine's own
                          per-step reasoning is precise and technical — it names
                          rule identifiers and internal codes — so it stays in
                          the audit record rather than on a merchant's screen. */}
                      <p className="mt-1 text-xs leading-relaxed text-ink-muted">
                        {stageMeaning(stage)}
                      </p>
                      <ul className="mt-2 space-y-1.5">
                        {stageEntries.map((entry) => (
                          <li
                            key={entry.id}
                            className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-lg border border-line bg-surface-raised/60 px-2.5 py-1.5"
                          >
                            <span className="text-micro text-ink">
                              {auditActionLabel(entry.action)}
                            </span>
                            <time
                              dateTime={entry.timestamp}
                              title={formatDateTime(entry.timestamp)}
                              className="tabular text-micro text-ink-subtle"
                            >
                              {formatRelative(entry.timestamp)}
                            </time>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </Card>
  );
}
