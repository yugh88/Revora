'use client';

import * as React from 'react';
import { Check, Loader2, RotateCcw, Save } from 'lucide-react';

import { formatDateTime, formatInrExact } from '../lib/api-client';
import { EVENT_TYPE_LABELS, type PolicyOut, type PolicyUpdate } from '../lib/types';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardDescription, CardHeader, CardTitle } from './ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { cn } from './ui/utils';

/**
 * Merchant-configurable bounds for one event type. BUILD_SPEC Section 4.
 *
 * Every value shown is read from GET /policies and every save goes to
 * PUT /policies. There is no copy of the thresholds in React — a form that
 * remembered its own numbers would drift from what the engine actually applies.
 *
 * Saving creates a NEW policy version rather than overwriting. That is stated in
 * the UI, because it is the reason a past decision's recorded justification does
 * not change when someone edits a threshold today.
 *
 * The validation bounds below mirror the backend's Pydantic constraints. They
 * exist to give immediate feedback, NOT to be the enforcement: the server
 * revalidates everything and its 422 is surfaced verbatim if the two ever
 * disagree.
 */

interface FieldSpec {
  key: keyof PolicyUpdate;
  label: string;
  help: string;
  kind: 'int' | 'money' | 'rate';
  min: number;
  max: number;
  step?: number;
  suffix?: string;
}

const FIELDS: FieldSpec[] = [
  {
    key: 'max_attempts',
    label: 'Max attempts',
    help: 'How many recovery attempts the engine may make on one event before stopping.',
    kind: 'int',
    min: 0,
    max: 10,
  },
  {
    key: 'cooldown_hours',
    label: 'Cooldown',
    help: 'Hours the engine must wait before contacting the same customer again.',
    kind: 'int',
    min: 0,
    max: 720,
    suffix: 'h',
  },
  {
    key: 'amount_threshold',
    label: 'Human handoff above',
    help: 'Above this amount the engine escalates to a human instead of acting automatically.',
    kind: 'money',
    min: 0,
    max: 100_000_000,
  },
  {
    key: 'recovery_probability_threshold',
    label: 'Min recovery probability',
    help: 'An action scoring below this is not worth taking and is blocked by the gate.',
    kind: 'rate',
    min: 0,
    max: 1,
    step: 0.01,
  },
  {
    key: 'contact_limit_per_channel',
    label: 'Contact limit per channel',
    help: 'Frequency cap. Also enforced before a Hinglish script may be generated.',
    kind: 'int',
    min: 0,
    max: 10,
  },
  {
    key: 'escalation_ceiling',
    label: 'Escalation ceiling',
    help: 'Highest level the engine may auto-escalate to. Section 6 caps this at L2.',
    kind: 'int',
    min: 0,
    max: 2,
    suffix: 'L',
  },
];

function toFormValues(policy: PolicyOut): PolicyUpdate {
  return {
    merchant_id: policy.merchant_id,
    event_type: policy.event_type,
    max_attempts: policy.max_attempts,
    cooldown_hours: policy.cooldown_hours,
    amount_threshold: policy.amount_threshold,
    recovery_probability_threshold: policy.recovery_probability_threshold,
    contact_limit_per_channel: policy.contact_limit_per_channel,
    escalation_ceiling: policy.escalation_ceiling,
  };
}

export function PolicyForm({
  policy,
  onSave,
  saving,
  savedVersion,
  errorMessage,
}: {
  policy: PolicyOut;
  onSave: (update: PolicyUpdate) => void;
  saving: boolean;
  savedVersion: number | null;
  errorMessage: string | null;
}) {
  const [values, setValues] = React.useState<PolicyUpdate>(() => toFormValues(policy));

  // Re-sync when the server sends a newer version, so the form always shows
  // what is actually stored rather than what was last typed.
  React.useEffect(() => {
    setValues(toFormValues(policy));
  }, [policy]);

  const dirty = React.useMemo(
    () => JSON.stringify(values) !== JSON.stringify(toFormValues(policy)),
    [values, policy],
  );

  const invalid = React.useMemo(() => {
    for (const field of FIELDS) {
      const raw = values[field.key];
      const numeric = typeof raw === 'string' ? Number.parseFloat(raw) : Number(raw);
      if (!Number.isFinite(numeric) || numeric < field.min || numeric > field.max) {
        return `${field.label} must be between ${field.min} and ${field.max}.`;
      }
    }
    return null;
  }, [values]);

  const update = (key: keyof PolicyUpdate, raw: string, kind: FieldSpec['kind']) => {
    setValues((current) => ({
      ...current,
      [key]:
        kind === 'money'
          ? raw
          : kind === 'rate'
            ? Number.parseFloat(raw || '0')
            : Number.parseInt(raw || '0', 10),
    }));
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>{EVENT_TYPE_LABELS[policy.event_type]}</CardTitle>
            <CardDescription>
              {policy.is_default
                ? 'Engine defaults — nothing saved for this event type yet.'
                : `Version ${policy.policy_version}${
                    policy.updated_at ? ` · saved ${formatDateTime(policy.updated_at)}` : ''
                  }`}
            </CardDescription>
          </div>
          <Badge variant={policy.is_default ? 'neutral' : 'accent'}>
            {policy.is_default ? 'Default' : `v${policy.policy_version}`}
          </Badge>
        </div>
      </CardHeader>

      <div className="px-5 pb-5">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {FIELDS.map((field) => {
            const raw = values[field.key];
            const inputId = `${policy.event_type}-${field.key}`;
            return (
              <div key={field.key}>
                <div className="flex items-center gap-1.5">
                  <label
                    htmlFor={inputId}
                    className="text-micro uppercase text-ink-subtle"
                  >
                    {field.label}
                  </label>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        aria-label={`What ${field.label} means`}
                        className="flex h-3.5 w-3.5 items-center justify-center rounded-full border border-line text-[8px] font-semibold text-ink-subtle hover:border-line-strong"
                      >
                        i
                      </button>
                    </TooltipTrigger>
                    <TooltipContent>{field.help}</TooltipContent>
                  </Tooltip>
                </div>
                <div className="relative mt-1.5">
                  {field.kind === 'money' ? (
                    <span
                      aria-hidden="true"
                      className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-xs text-ink-subtle"
                    >
                      ₹
                    </span>
                  ) : null}
                  <input
                    id={inputId}
                    type="number"
                    inputMode="decimal"
                    disabled={saving}
                    min={field.min}
                    max={field.max}
                    step={field.step ?? 1}
                    value={String(raw)}
                    onChange={(event) => update(field.key, event.target.value, field.kind)}
                    className={cn(
                      'tabular h-9 w-full rounded-lg border border-line bg-surface pr-3 text-xs text-ink outline-none transition-colors',
                      'focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30',
                      'disabled:cursor-not-allowed disabled:opacity-60',
                      field.kind === 'money' ? 'pl-6' : 'pl-3',
                    )}
                  />
                  {field.suffix && field.kind !== 'money' ? (
                    <span
                      aria-hidden="true"
                      className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-micro text-ink-subtle"
                    >
                      {field.suffix}
                    </span>
                  ) : null}
                </div>
                {field.kind === 'money' ? (
                  <p className="tabular mt-1 text-micro text-ink-subtle">
                    {formatInrExact(String(raw))}
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>

        {invalid ? (
          <p className="mt-3 rounded-lg border border-pending/25 bg-pending/5 px-3 py-2 text-xs text-ink-muted">
            {invalid}
          </p>
        ) : null}

        {errorMessage ? (
          <p className="mt-3 rounded-lg border border-unrecoverable/25 bg-unrecoverable/5 px-3 py-2 text-xs text-ink-muted">
            {errorMessage}
          </p>
        ) : null}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            disabled={!dirty || saving || Boolean(invalid)}
            onClick={() => onSave(values)}
          >
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Save className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            Save as new version
          </Button>

          {dirty ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={saving}
              onClick={() => setValues(toFormValues(policy))}
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              Reset
            </Button>
          ) : null}

          {savedVersion !== null && !dirty ? (
            <span className="inline-flex items-center gap-1.5 text-xs text-recovered">
              <Check className="h-3.5 w-3.5" aria-hidden="true" />
              Saved as version {savedVersion}
            </span>
          ) : null}

          <p className="ml-auto text-micro text-ink-subtle">
            Saving creates a new version; earlier decisions keep the policy that gated them.
          </p>
        </div>
      </div>
    </Card>
  );
}
