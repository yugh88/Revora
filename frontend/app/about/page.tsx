'use client';

import * as React from 'react';

import { Card } from '../../components/ui/card';
import { AppShell } from '../../components/ui/site-header';
import { cn } from '../../components/ui/utils';

/**
 * About Revora — how the system actually works.
 *
 * Built as an interactive diagram rather than a static image so it stays
 * honest: every card below describes a component that genuinely exists in this
 * codebase, and the tech listed on it is what that component genuinely uses.
 * A picture exported from a drawing tool drifts from the code within a week;
 * this one sits next to it.
 *
 * Hovering a stage highlights it, dims the rest, and surfaces its stack — so a
 * judge can follow one path through the system without reading all of it at
 * once.
 */

interface Stage {
  id: string;
  step: string;
  title: string;
  what: string;
  why: string;
  tech: string[];
  tone: 'detect' | 'think' | 'gate' | 'act' | 'verify';
}

const STAGES: Stage[] = [
  {
    id: 'detect',
    step: '01',
    title: 'Detect',
    what: 'Payment events arrive and become recovery cases — failed charges, stalled checkouts, ageing invoices, broken mandates.',
    why: 'Revora processes them on its own. Nobody presses a button, which is what makes it an agent rather than a report.',
    tech: ['FastAPI', 'SQLAlchemy', 'SQLite', 'asyncio background task'],
    tone: 'detect',
  },
  {
    id: 'diagnose',
    step: '02',
    title: 'Diagnose',
    what: 'Works out why the money is at risk: card expired, insufficient funds, issuer declined, cash-flow delay.',
    why: 'A rule engine decides. A decision tree offers a second opinion that is recorded but never overrides — the rules stay authoritative.',
    tech: ['Rule engine', 'scikit-learn (advisory only)'],
    tone: 'think',
  },
  {
    id: 'decide',
    step: '03',
    title: 'Decide',
    what: 'Scores every permitted action by expected value: recovery probability × amount, minus cost and customer annoyance.',
    why: 'Adjusted by what is known about this payer — success rate, payment delay, promises kept. Bounded, so history tilts a decision without taking it over.',
    tech: ['Probability engine', 'Decision engine'],
    tone: 'think',
  },
  {
    id: 'policy',
    step: '04',
    title: 'Your policy decides what is allowed',
    what: 'Attempt limits, cooldowns, contact caps, amount thresholds, do-not-contact, and open promises.',
    why: 'Authoritative over everything upstream. A high-scoring action your policy forbids simply does not happen — and no model can appeal it.',
    tech: ['Policy engine', 'Stopping rules', 'State machine'],
    tone: 'gate',
  },
  {
    id: 'context',
    step: '05',
    title: 'Retrieve context',
    what: 'Gathers this customer’s history: past messages, their replies, earlier promises, payment behaviour.',
    why: 'Strictly one customer, filtered in SQL. Context only — it shapes wording, never a decision, and quoted replies are treated as data, never instructions.',
    tech: ['Retrieval layer (RAG)', 'SQLite'],
    tone: 'think',
  },
  {
    id: 'compose',
    step: '06',
    title: 'Write the message',
    what: 'Builds the recovery message from templates, then checks it against contact hours, frequency caps, urgency limits and language rules.',
    why: 'Compliance is a gate, not a warning. A refused message carries no text at all, so it cannot be copied and sent anyway.',
    tech: ['Template engine', 'YAML compliance rules'],
    tone: 'gate',
  },
  {
    id: 'language',
    step: '07',
    title: 'Make it sound human',
    what: 'Rewrites the approved message into natural Hinglish.',
    why: 'A language layer only. It receives an already-approved script, its output is re-checked, and if it is slow, offline or inventive the original template is used unchanged.',
    tech: ['Ollama', 'Mistral', 'YAML fallback'],
    tone: 'act',
  },
  {
    id: 'reach',
    step: '08',
    title: 'Reach the customer',
    what: 'Chooses email, SMS or a call based on the action, the amount and how this customer has responded before.',
    why: 'Revora picks the channel, so a merchant never has to. In this build every send is simulated — no provider is connected and no customer is contacted.',
    tech: ['Channel recommender', 'Communication log'],
    tone: 'act',
  },
  {
    id: 'promise',
    step: '09',
    title: 'Track what they promise',
    what: 'Reads the reply. “Main kal payment kar dunga” becomes a tracked commitment with a real date.',
    why: 'Deterministic parsing, English and Hinglish. If no date was stated, no date is invented — and recovery pauses until the promised day, then resumes on its own.',
    tech: ['Response interpreter', 'Promise lifecycle'],
    tone: 'act',
  },
  {
    id: 'verify',
    step: '10',
    title: 'Verify and record',
    what: 'Confirms whether the money actually arrived, writes the recovery ledger, and records every step in the audit trail.',
    why: 'Money is only ever counted once, in one place, as exact integer paise. Recovered, in progress and written off always sum to the amount at risk.',
    tech: ['Recovery ledger', 'Audit trail', 'Razorpay Test Mode'],
    tone: 'verify',
  },
];

const TONE_RING: Record<Stage['tone'], string> = {
  detect: 'ring-accent/40 bg-accent/[0.06]',
  think: 'ring-pending/40 bg-pending/[0.06]',
  gate: 'ring-unrecoverable/40 bg-unrecoverable/[0.06]',
  act: 'ring-accent/40 bg-accent/[0.06]',
  verify: 'ring-recovered/40 bg-recovered/[0.06]',
};

const TONE_TEXT: Record<Stage['tone'], string> = {
  detect: 'text-accent',
  think: 'text-pending',
  gate: 'text-unrecoverable',
  act: 'text-accent',
  verify: 'text-recovered',
};

const LAYERS: Array<{ name: string; items: string[] }> = [
  { name: 'Interface', items: ['Next.js 14', 'React', 'TypeScript', 'Tailwind CSS'] },
  { name: 'API', items: ['FastAPI', 'Pydantic', 'Uvicorn'] },
  {
    name: 'Recovery engine',
    items: [
      'Rule-based diagnosis',
      'Probability scoring',
      'Policy engine',
      'Stopping rules',
      'State machine',
    ],
  },
  { name: 'Language', items: ['Ollama', 'Mistral', 'YAML templates', 'Compliance rules'] },
  { name: 'Orchestration', items: ['LangGraph', 'Retrieval layer'] },
  { name: 'Data', items: ['SQLAlchemy', 'SQLite', 'Redis (optional)'] },
  { name: 'Payments', items: ['Razorpay Test Mode', 'Simulation gateway'] },
];

export default function AboutPage() {
  const [active, setActive] = React.useState<string | null>(null);

  return (
    <AppShell>
      <main className="mx-auto max-w-[1240px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up">
          <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-[28px]">
            How Revora works
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
            Revenue slips away quietly — a card expires, a checkout stalls, an invoice
            ages. Revora finds each case, works out why, decides what to do within your
            limits, acts, and checks whether the money came back.
          </p>
          <p className="mt-3 text-xs text-ink-subtle">
            Hover any step to see what it does and what it is built with.
          </p>
        </div>

        {/* ---------------- The flow ---------------- */}
        <ol
          className="animate-fade-up stagger-1 mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
          onMouseLeave={() => setActive(null)}
        >
          {STAGES.map((stage) => {
            const isActive = active === stage.id;
            const dimmed = active !== null && !isActive;
            return (
              <li key={stage.id}>
                <button
                  type="button"
                  onMouseEnter={() => setActive(stage.id)}
                  onFocus={() => setActive(stage.id)}
                  onClick={() => setActive(isActive ? null : stage.id)}
                  aria-expanded={isActive}
                  className={cn(
                    'h-full w-full rounded-card border border-line bg-surface p-4 text-left',
                    'outline-none transition-all duration-200',
                    'focus-visible:ring-2 focus-visible:ring-accent',
                    isActive && `ring-2 ${TONE_RING[stage.tone]} shadow-card-hover`,
                    dimmed && 'opacity-40',
                    !isActive && !dimmed && 'hover:border-line-strong',
                  )}
                >
                  <div className="flex items-baseline gap-2">
                    <span
                      className={cn(
                        'tabular text-micro font-bold',
                        isActive ? TONE_TEXT[stage.tone] : 'text-ink-subtle',
                      )}
                    >
                      {stage.step}
                    </span>
                    <span className="text-sm font-semibold text-ink">{stage.title}</span>
                  </div>

                  <p className="mt-2 text-xs leading-relaxed text-ink-muted">
                    {stage.what}
                  </p>

                  {/* Revealed on hover: the reasoning and the stack. */}
                  <div
                    className={cn(
                      'grid transition-all duration-200',
                      isActive
                        ? 'mt-2.5 grid-rows-[1fr] opacity-100'
                        : 'grid-rows-[0fr] opacity-0',
                    )}
                  >
                    <div className="overflow-hidden">
                      <p className="border-t border-line pt-2.5 text-xs leading-relaxed text-ink-muted">
                        {stage.why}
                      </p>
                      <div className="mt-2.5 flex flex-wrap gap-1">
                        {stage.tech.map((item) => (
                          <span
                            key={item}
                            className={cn(
                              'rounded-md px-1.5 py-0.5 text-micro font-medium ring-1',
                              TONE_RING[stage.tone],
                              TONE_TEXT[stage.tone],
                            )}
                          >
                            {item}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </button>
              </li>
            );
          })}
        </ol>

        {/* ---------------- What holds it together ---------------- */}
        <section className="animate-fade-up stagger-2 mt-8">
          <h2 className="text-sm font-semibold tracking-tight text-ink">
            The rules that never bend
          </h2>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {[
              {
                title: 'Your policy always wins',
                body: 'Scoring proposes; policy disposes. No model, and no amount of context, can talk Revora past a limit you set.',
              },
              {
                title: 'Money is counted once',
                body: 'One place in the code records a rupee as recovered. Recovered, in progress and written off always sum to the amount at risk, to the paisa.',
              },
              {
                title: 'Compliance is a gate',
                body: 'A message that fails a check is not written at all. There is no draft to override and nothing to copy out.',
              },
              {
                title: 'Nothing is invented',
                body: 'No date the customer did not state, no delivery that did not happen, no figure the ledger cannot support.',
              },
            ].map((rule) => (
              <Card key={rule.title} className="p-4">
                <p className="text-sm font-semibold text-ink">{rule.title}</p>
                <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">{rule.body}</p>
              </Card>
            ))}
          </div>
        </section>

        {/* ---------------- Stack by layer ---------------- */}
        <section className="animate-fade-up stagger-3 mt-8">
          <h2 className="text-sm font-semibold tracking-tight text-ink">Built with</h2>
          <Card className="mt-3 p-5">
            <dl className="grid grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
              {LAYERS.map((layer) => (
                <div key={layer.name}>
                  <dt className="text-micro font-semibold uppercase tracking-wide text-ink-subtle">
                    {layer.name}
                  </dt>
                  <dd className="mt-1.5 flex flex-wrap gap-1">
                    {layer.items.map((item) => (
                      <span
                        key={item}
                        className="rounded-md border border-line bg-surface-raised px-1.5 py-0.5 text-micro text-ink-muted"
                      >
                        {item}
                      </span>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          </Card>
        </section>

        <p className="animate-fade-up stagger-3 mt-6 text-xs leading-relaxed text-ink-subtle">
          Demo environment. Payments run against Razorpay Test Mode or a local
          simulation, and no customer is ever contacted.
        </p>
      </main>
    </AppShell>
  );
}
