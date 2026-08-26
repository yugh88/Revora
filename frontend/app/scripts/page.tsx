'use client';

import * as React from 'react';
import Link from 'next/link';
import {
  AlertCircle,
  ArrowRight,
  Check,
  Loader2,
  MessageSquareText,
  RotateCcw,
  ShieldCheck,
  ShieldX,
  X,
} from 'lucide-react';

import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { SiteHeader } from '../../components/ui/site-header';
import { cn } from '../../components/ui/utils';
import { api, ApiError, formatInrExact, humanizeKey } from '../../lib/api-client';
import { EVENT_TYPE_LABELS, type EventSummary, type ScriptResponse } from '../../lib/types';

/**
 * Hinglish recovery scripts. BUILD_SPEC Sections 7 and 13.
 *
 * Everything shown comes from GET /scripts/{event_id}. No example text is
 * hardcoded here — if the backend refuses to generate a script, this page shows
 * the refusal and which rule caused it, not a placeholder.
 *
 * Generating is an inspection: the endpoint writes nothing, so opening this page
 * cannot change what the engine has done.
 */
export default function ScriptsPage() {
  const [events, setEvents] = React.useState<EventSummary[] | null>(null);
  const [selected, setSelected] = React.useState<string | null>(null);
  const [script, setScript] = React.useState<ScriptResponse | null>(null);
  const [loadingList, setLoadingList] = React.useState(true);
  const [loadingScript, setLoadingScript] = React.useState(false);
  const [error, setError] = React.useState<ApiError | null>(null);

  const loadEvents = React.useCallback(async () => {
    setLoadingList(true);
    try {
      const response = await api.listEvents({ limit: 40 });
      setEvents(response.items);
      setSelected((current) => current ?? response.items[0]?.id ?? null);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError('Could not load events.'));
    } finally {
      setLoadingList(false);
    }
  }, []);

  React.useEffect(() => {
    void loadEvents();
  }, [loadEvents]);

  React.useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoadingScript(true);
    api
      .getScript(selected)
      .then((response) => {
        if (!cancelled) {
          setScript(response);
          setError(null);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setError(caught instanceof ApiError ? caught : new ApiError('Could not generate.'));
          setScript(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingScript(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  return (
    <div className="min-h-screen">
      <SiteHeader />
      <main className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Recovery scripts</h1>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
            Hinglish scripts generated from YAML templates and the engine&rsquo;s own decision
            factors — never a language model. Every script is compliance-checked before it
            exists, and a refused one produces no text at all.
          </p>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-5 lg:grid-cols-3">
          {/* Event picker */}
          <div className="lg:col-span-1">
            <Card className="animate-fade-up stagger-1 flex max-h-[70vh] flex-col overflow-hidden">
              <CardHeader>
                <CardTitle>Events</CardTitle>
                <CardDescription>Pick an event to see the script Revora would use.</CardDescription>
              </CardHeader>
              {loadingList && !events ? (
                <div className="space-y-2 px-5 pb-5" role="status" aria-busy="true">
                  <span className="sr-only">Loading events</span>
                  {Array.from({ length: 8 }).map((_, index) => (
                    <div key={index} className="h-10 animate-pulse rounded-lg bg-line/60" />
                  ))}
                </div>
              ) : !events || events.length === 0 ? (
                <div className="px-5 pb-6 text-center">
                  <p className="text-sm text-ink-muted">No events yet.</p>
                  <Button asChild size="sm" className="mt-3">
                    <Link href="/batch">Run a recovery analysis</Link>
                  </Button>
                </div>
              ) : (
                <ul className="flex-1 space-y-1 overflow-y-auto px-3 pb-4">
                  {events.map((event) => (
                    <li key={event.id}>
                      <button
                        type="button"
                        onClick={() => setSelected(event.id)}
                        aria-current={selected === event.id ? 'true' : undefined}
                        className={cn(
                          'w-full rounded-lg px-2.5 py-2 text-left transition-colors',
                          'outline-none focus-visible:ring-2 focus-visible:ring-accent',
                          selected === event.id
                            ? 'bg-accent/[0.07] ring-1 ring-accent/25'
                            : 'hover:bg-surface-raised',
                        )}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <code className="truncate text-xs font-medium text-ink">{event.id}</code>
                          <span className="tabular shrink-0 text-micro text-ink-subtle">
                            {formatInrExact(event.amount)}
                          </span>
                        </div>
                        <p className="mt-0.5 truncate text-micro text-ink-subtle">
                          {EVENT_TYPE_LABELS[event.type]} ·{' '}
                          {event.root_cause ? humanizeKey(event.root_cause) : 'undiagnosed'}
                        </p>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          {/* Script detail */}
          <div className="animate-fade-up stagger-2 lg:col-span-2">
            {error ? (
              <ErrorState error={error} onRetry={() => void loadEvents()} busy={loadingList} />
            ) : loadingScript && !script ? (
              <Card className="p-6" role="status" aria-busy="true">
                <span className="sr-only">Generating script</span>
                <div className="h-3.5 w-40 animate-pulse rounded bg-line/60" />
                <div className="mt-4 h-24 w-full animate-pulse rounded-lg bg-line/60" />
              </Card>
            ) : script ? (
              <ScriptDetail script={script} busy={loadingScript} />
            ) : (
              <Card className="flex flex-col items-center px-6 py-16 text-center">
                <MessageSquareText className="h-6 w-6 text-ink-subtle" aria-hidden="true" />
                <p className="mt-3 text-sm text-ink-muted">Select an event to generate its script.</p>
              </Card>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

function ScriptDetail({ script, busy }: { script: ScriptResponse; busy: boolean }) {
  return (
    <div className={cn('space-y-5', busy && 'opacity-60')}>
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <CardTitle>
                {script.compliant ? 'Generated script' : 'Script withheld'}
              </CardTitle>
              <CardDescription>
                {script.compliant
                  ? `Rendered from ${script.template_key} — a YAML template, not a model.`
                  : 'A compliance rule refused this request, so no text was rendered.'}
              </CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant={script.compliant ? 'recovered' : 'unrecoverable'}>
                {script.compliant ? (
                  <ShieldCheck className="h-3 w-3" aria-hidden="true" />
                ) : (
                  <ShieldX className="h-3 w-3" aria-hidden="true" />
                )}
                {script.compliant ? 'Compliant' : 'Refused'}
              </Badge>
              <Badge variant="neutral">{script.tone}</Badge>
              <Badge variant={script.urgency === 'high' ? 'pending' : 'neutral'}>
                {script.urgency} urgency
              </Badge>
            </div>
          </div>
        </CardHeader>

        <div className="px-5 pb-5">
          {script.compliant ? (
            <blockquote className="rounded-lg border border-accent/25 bg-accent/[0.04] px-4 py-3.5 text-sm leading-relaxed text-ink">
              {script.script}
            </blockquote>
          ) : (
            <div className="rounded-lg border border-unrecoverable/25 bg-unrecoverable/5 px-4 py-3.5">
              <p className="text-sm font-medium text-ink">No script was generated.</p>
              <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
                {script.failure_reason}
              </p>
              <p className="mt-2 text-xs leading-relaxed text-ink-subtle">
                Revora does not render text it may not send. There is no draft to override.
              </p>
            </div>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-micro uppercase text-ink-subtle">
            <span>
              Channel <span className="text-ink">{humanizeKey(script.channel)}</span>
            </span>
            <span>
              Language <span className="text-ink">{script.language}</span>
            </span>
            <Link
              href={`/events/${script.event_id}`}
              className="ml-auto inline-flex items-center gap-1 rounded normal-case text-accent outline-none hover:underline focus-visible:ring-2 focus-visible:ring-accent"
            >
              Open event
              <ArrowRight className="h-3 w-3" aria-hidden="true" />
            </Link>
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Why this action</CardTitle>
          <CardDescription>
            Assembled from the decision&rsquo;s own recorded factors.
          </CardDescription>
        </CardHeader>
        <div className="px-5 pb-5">
          <p className="text-sm leading-relaxed text-ink-muted">{script.reasoning}</p>
        </div>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Compliance checks</CardTitle>
          <CardDescription>
            All four Section 7 rules, recorded whether they passed or failed.
          </CardDescription>
        </CardHeader>
        <div className="px-5 pb-5">
          <ul className="space-y-2.5">
            {script.compliance_checks.map((check) => (
              <li
                key={check.rule_id}
                className={cn(
                  'rounded-lg border px-3 py-2.5',
                  check.passed
                    ? 'border-line bg-surface-raised/50'
                    : 'border-unrecoverable/25 bg-unrecoverable/5',
                )}
              >
                <div className="flex items-start gap-2">
                  <span
                    className={cn(
                      'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full',
                      check.passed
                        ? 'bg-recovered/15 text-recovered'
                        : 'bg-unrecoverable/15 text-unrecoverable',
                    )}
                    aria-hidden="true"
                  >
                    {check.passed ? (
                      <Check className="h-2.5 w-2.5" strokeWidth={3} />
                    ) : (
                      <X className="h-2.5 w-2.5" strokeWidth={3} />
                    )}
                  </span>
                  <div className="min-w-0">
                    <p className="text-xs font-semibold text-ink">
                      {humanizeKey(check.rule_id)}
                      <span className="sr-only">{check.passed ? ': passed' : ': failed'}</span>
                    </p>
                    <p className="mt-0.5 text-micro text-ink-subtle">{check.description}</p>
                    <p className="mt-1 text-xs leading-relaxed text-ink-muted">{check.detail}</p>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </Card>
    </div>
  );
}

function ErrorState({ error, onRetry, busy }: { error: ApiError; onRetry: () => void; busy: boolean }) {
  return (
    <Card className="border-unrecoverable/25">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-start">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-unrecoverable/10 ring-1 ring-unrecoverable/20">
          <AlertCircle className="h-5 w-5 text-unrecoverable" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink">Could not generate a script</h2>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">{error.userMessage}</p>
          <Button variant="secondary" size="sm" className="mt-4" onClick={onRetry} disabled={busy}>
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            Try again
          </Button>
        </div>
      </div>
    </Card>
  );
}
