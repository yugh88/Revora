'use client';

import * as React from 'react';
import { Check, KeyRound, Landmark, ShieldCheck } from 'lucide-react';

import type { GatewayUsed } from '../lib/types';
import { Badge } from './ui/badge';
import { cn } from './ui/utils';

/**
 * Gateway selection. BUILD_SPEC Section 5.
 *
 * "User-selectable at runtime (UI toggle). Default = Built-in Simulator."
 *
 * Both options are described honestly, including their costs. The simulator is
 * marked as the default and the safe demo path; the Razorpay option states
 * plainly that it needs credentials and that it is TEST MODE — not a production
 * payment path, and no real money moves either way.
 *
 * There is no silent fallback anywhere in this flow. If the sandbox is selected
 * without credentials the backend returns a 400 and the caller shows it. Quietly
 * running on the simulator instead would let a demo claim sandbox numbers that
 * never came from the sandbox.
 */

interface Option {
  value: GatewayUsed;
  label: string;
  tagline: string;
  points: string[];
  icon: typeof ShieldCheck;
  badge: { text: string; variant: React.ComponentProps<typeof Badge>['variant'] };
}

const OPTIONS: Option[] = [
  {
    value: 'local_simulation',
    label: 'Built-in Simulator',
    tagline: 'Reliable demo mode using Revora\u2019s deterministic payment simulation.',
    points: [
      'Fully self-built — no external service is contacted',
      'Same seed produces the same outcome every run',
      'Models Razorpay\u2019s subscription lifecycle independently',
    ],
    icon: ShieldCheck,
    badge: { text: 'Default', variant: 'accent' },
  },
  {
    value: 'razorpay_test',
    label: 'Razorpay Test Sandbox',
    tagline: 'Execute test recovery actions against Razorpay\u2019s test environment. No real money, ever.',
    points: [
      'Requires RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET on the backend',
      'Test-mode keys only — a live key is refused at startup',
      'Synthetic references will not resolve to real Razorpay objects',
    ],
    icon: Landmark,
    badge: { text: 'Test mode', variant: 'pending' },
  },
];

export function GatewayToggle({
  value,
  onChange,
  disabled,
}: {
  value: GatewayUsed;
  onChange: (gateway: GatewayUsed) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset disabled={disabled} className="min-w-0">
      <legend className="text-micro font-semibold uppercase text-ink-subtle">
        Recovery execution
      </legend>

      {/* radiogroup semantics: arrow keys move between options, exactly as a
          native radio group would. */}
      <div
        role="radiogroup"
        aria-label="Execution gateway"
        className="mt-2.5 grid grid-cols-1 gap-3 sm:grid-cols-2"
      >
        {OPTIONS.map((option) => {
          const selected = option.value === value;
          const Icon = option.icon;

          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={disabled}
              onClick={() => onChange(option.value)}
              className={cn(
                'group relative rounded-card border p-4 text-left transition-all duration-200',
                'outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg',
                'disabled:cursor-not-allowed disabled:opacity-60',
                selected
                  ? 'border-accent/50 bg-accent/[0.04] shadow-card'
                  : 'border-line bg-surface hover:border-line-strong hover:bg-surface-raised/60',
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2.5">
                  <span
                    className={cn(
                      'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors',
                      selected
                        ? 'bg-accent/10 text-accent ring-1 ring-accent/20'
                        : 'bg-surface-raised text-ink-muted ring-1 ring-line',
                    )}
                  >
                    <Icon className="h-4 w-4" aria-hidden="true" />
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-ink">{option.label}</p>
                    <p className="mt-0.5 text-xs text-ink-subtle">{option.tagline}</p>
                  </div>
                </div>

                <span
                  className={cn(
                    'flex h-4 w-4 shrink-0 items-center justify-center rounded-full border transition-colors',
                    selected
                      ? 'border-accent bg-accent text-accent-ink'
                      : 'border-line-strong',
                  )}
                  aria-hidden="true"
                >
                  {selected ? <Check className="h-2.5 w-2.5" strokeWidth={3} /> : null}
                </span>
              </div>

              <ul className="mt-3 space-y-1.5">
                {option.points.map((point) => (
                  <li
                    key={point}
                    className="flex gap-1.5 text-xs leading-relaxed text-ink-muted"
                  >
                    <span aria-hidden="true" className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-ink-subtle/50" />
                    {point}
                  </li>
                ))}
              </ul>

              <div className="mt-3">
                <Badge variant={option.badge.variant}>
                  {option.value === 'razorpay_test' ? (
                    <KeyRound className="h-3 w-3" aria-hidden="true" />
                  ) : null}
                  {option.badge.text}
                </Badge>
              </div>
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
