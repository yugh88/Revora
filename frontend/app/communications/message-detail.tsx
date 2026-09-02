'use client';

import * as React from 'react';

import { cn } from '../../components/ui/utils';
import { complianceRuleLabel, contactLabel, toneLabel, urgencyLabel } from '../../lib/labels';
import type { ScriptResponse } from '../../lib/types';

/**
 * Why this message, in this tone, and whether it is allowed.
 *
 * Every compliance rule is listed — the ones that passed as well as the one
 * that refused. A merchant who only ever sees failures has no way to know the
 * checks are running at all.
 */
export function MessageDetail({ script }: { script: ScriptResponse }) {
  return (
    <>
      <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-line pt-4">
        <div>
          <dt className="text-micro uppercase text-ink-subtle">Tone</dt>
          <dd className="mt-0.5 text-xs font-medium text-ink">{toneLabel(script.tone)}</dd>
        </div>
        <div>
          <dt className="text-micro uppercase text-ink-subtle">Urgency</dt>
          <dd className="mt-0.5 text-xs font-medium text-ink">
            {urgencyLabel(script.urgency)}
          </dd>
        </div>
        <div>
          <dt className="text-micro uppercase text-ink-subtle">Channel</dt>
          <dd className="mt-0.5 text-xs font-medium text-ink">
            {contactLabel(script.channel)}
          </dd>
        </div>
      </dl>

      {script.reasoning ? (
        <div className="mt-3 rounded-lg bg-surface-raised/60 px-3 py-2.5">
          <p className="text-micro uppercase text-ink-subtle">
            Why Revora would contact this customer
          </p>
          <p className="mt-1 text-xs leading-relaxed text-ink-muted">{script.reasoning}</p>
        </div>
      ) : null}

      {script.compliance_checks?.length ? (
        <div className="mt-3">
          <p className="text-micro uppercase text-ink-subtle">Is this message allowed?</p>
          <ul className="mt-1.5 space-y-1">
            {script.compliance_checks.map((check, index) => (
              <li
                key={`${check.rule_id}-${index}`}
                className="flex items-start gap-2 rounded-md bg-surface-raised/50 px-2 py-1.5"
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    'mt-1 h-1.5 w-1.5 shrink-0 rounded-full',
                    check.passed ? 'bg-recovered' : 'bg-unrecoverable',
                  )}
                />
                <span className="min-w-0">
                  <span className="text-micro font-medium text-ink">
                    {complianceRuleLabel(check.rule_id)}
                  </span>
                  {!check.passed && check.detail ? (
                    <span className="mt-0.5 block text-micro leading-relaxed text-ink-muted">
                      {check.detail}
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  );
}
