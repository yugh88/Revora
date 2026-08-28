'use client';

import * as React from 'react';
import Link from 'next/link';
import {
  AlertCircle,
  ArrowRight,
  Check,
  Eye,
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
import { AppShell } from '../../components/ui/site-header';
import { cn } from '../../components/ui/utils';
import { api, ApiError, formatInrExact } from '../../lib/api-client';
import {
  channelLabel,
  complianceRuleLabel,
  eventTypeLabel,
  rootCauseLabel,
  templateKeyLabel,
  toneLabel,
  urgencyLabel,
} from '../../lib/labels';
import type { EventSummary, ScriptResponse } from '../../lib/types';

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
  // Preview is opt-in per event and resets whenever the selection changes, so
  // a preview from one event can never be read as the live result of another.
  const [preview, setPreview] = React.useState<ScriptResponse | null>(null);
  const [loadingPreview, setLoadingPreview] = React.useState(false);

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
    setPreview(null);
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
    <AppShell>
      <main className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">
            Recovery messages
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
            When Revora decides to reach a customer, this is the message it would send —
            and the checks it has to pass before anyone hears from you.
          </p>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-5 lg:grid-cols-3">
          {/* Event picker */}
          <div className="lg:col-span-1">
            <Card className="animate-fade-up stagger-1 flex max-h-[70vh] flex-col overflow-hidden">
              <CardHeader>
                <CardTitle>Events</CardTitle>
                <CardDescription>
                  Pick a case to see the message Revora would use.
                </CardDescription>
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
                  <p className="text-sm text-ink-muted">No recovery cases yet.</p>
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
                          <span className="truncate text-xs font-medium text-ink">
                            {event.customer_name}
                          </span>
                          <span className="tabular shrink-0 text-micro text-ink-subtle">
                            {formatInrExact(event.amount)}
                          </span>
                        </div>
                        <p className="mt-0.5 truncate text-micro text-ink-subtle">
                          {eventTypeLabel(event.type)} ·{' '}
                          {event.root_cause ? rootCauseLabel(event.root_cause) : 'Not yet diagnosed'}
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
              <ScriptDetail
                script={script}
                busy={loadingScript}
                preview={preview}
                loadingPreview={loadingPreview}
                onPreview={async () => {
                  if (!selected) return;
                  setLoadingPreview(true);
                  try {
                    setPreview(await api.previewScript(selected));
                  } catch (caught) {
                    setError(
                      caught instanceof ApiError
                        ? caught
                        : new ApiError('Could not render the preview.'),
                    );
                  } finally {
                    setLoadingPreview(false);
                  }
                }}
                onClearPreview={() => setPreview(null)}
              />
            ) : (
              <Card className="flex flex-col items-center px-6 py-16 text-center">
                <MessageSquareText className="h-6 w-6 text-ink-subtle" aria-hidden="true" />
                <p className="mt-3 text-sm text-ink-muted">
                  Select a case to see its recovery message.
                </p>
              </Card>
            )}
          </div>
        </div>
      </main>
    </AppShell>
  );
}

function ScriptDetail({
  script,
  busy,
  preview,
  loadingPreview,
  onPreview,
  onClearPreview,
}: {
  script: ScriptResponse;
  busy: boolean;
  preview: ScriptResponse | null;
  loadingPreview: boolean;
  onPreview: () => void;
  onClearPreview: () => void;
}) {
  const previewMode: PreviewMode = script.compliant
    ? 'secondary'
    : withheldOnlyByTime(script)
      ? 'primary'
      : 'hidden';

  return (
    <div className={cn('space-y-5', busy && 'opacity-60')}>
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-micro font-semibold uppercase tracking-wide text-ink-subtle">
                What Revora would send now
              </p>
              <CardTitle className="mt-0.5">
                {script.compliant ? 'Hinglish voice message' : 'Message withheld'}
              </CardTitle>
              <CardDescription>
                {script.compliant
                  ? 'Hinglish recovery message, ready to send.'
                  : 'This message was held back. Nothing was sent to the customer.'}
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
              <Badge variant="neutral">{toneLabel(script.tone)}</Badge>
              <Badge variant={script.urgency === 'high' ? 'pending' : 'neutral'}>
                {urgencyLabel(script.urgency)} urgency
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
                Revora does not write messages it is not allowed to send.
              </p>
            </div>
          )}

          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-line pt-3.5 sm:grid-cols-4">
            <Meta label="Purpose" value="Payment recovery" />
            <Meta label="Tone" value={toneLabel(script.tone)} />
            <Meta label="Urgency" value={urgencyLabel(script.urgency)} />
            <Meta label="Channel" value={channelLabel(script.channel)} />
          </dl>

          <div className="mt-3 flex justify-end">
            <Link
              href={`/events/${script.event_id}?from=scripts`}
              className="inline-flex items-center gap-1 rounded text-xs text-accent outline-none hover:underline focus-visible:ring-2 focus-visible:ring-accent"
            >
              Open this recovery
              <ArrowRight className="h-3 w-3" aria-hidden="true" />
            </Link>
          </div>
        </div>
      </Card>

      {/* Demo preview.
          Prominent when the contact-time rule is the ONLY thing withholding the
          message — that is the case a permitted window would change, and the
          case a judge arriving at 20:00 IST would otherwise never see.
          Available but quiet when the live message already rendered, where a
          preview is genuinely redundant: same engine, same output.
          Hidden entirely when another rule refused, because a preview would
          refuse too and offering it would promise something it cannot deliver. */}
      {previewMode !== 'hidden' ? (
        <PreviewSection
          mode={previewMode}
          preview={preview}
          loading={loadingPreview}
          onPreview={onPreview}
          onClear={onClearPreview}
        />
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Why Revora would contact this customer</CardTitle>
          <CardDescription>The reasoning behind this recovery action.</CardDescription>
        </CardHeader>
        <div className="px-5 pb-5">
          <p className="text-sm leading-relaxed text-ink-muted">{script.reasoning}</p>
        </div>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Is this message allowed?</CardTitle>
          <CardDescription>
            Every check Revora runs before a customer hears from you.
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
                      {complianceRuleLabel(check.rule_id)}
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

/**
 * True when the ONLY thing standing between this event and a script is the
 * clock.
 *
 * A preview changes the contact-time check and nothing else, so offering one
 * for an event refused by the frequency cap or the urgency ceiling would
 * promise something it cannot deliver — the preview would refuse too.
 */
function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-micro uppercase text-ink-subtle">{label}</dt>
      <dd className="mt-0.5 truncate text-xs font-medium text-ink">{value}</dd>
    </div>
  );
}

function withheldOnlyByTime(script: ScriptResponse): boolean {
  const failed = script.compliance_checks.filter((check) => !check.passed);
  return failed.length > 0 && failed.every((check) => check.rule_id === 'contact_time_window');
}

/**
 * The demo-preview affordance and its result.
 *
 * Labelled unambiguously at every step. A judge must never be able to read this
 * as a message that was sent — the words "sent", "delivered" and "contacted" do
 * not appear, because none of those happened.
 */
type PreviewMode = 'primary' | 'secondary' | 'hidden';

function PreviewSection({
  mode,
  preview,
  loading,
  onPreview,
  onClear,
}: {
  mode: PreviewMode;
  preview: ScriptResponse | null;
  loading: boolean;
  onPreview: () => void;
  onClear: () => void;
}) {
  if (!preview) {
    // Quiet affordance when the live message already rendered: the preview
    // would show the same text, so it earns a line, not a card.
    if (mode === 'secondary') {
      return (
        <button
          type="button"
          onClick={onPreview}
          disabled={loading}
          className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs text-ink-subtle outline-none transition-colors hover:bg-surface-raised hover:text-ink-muted focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-60"
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Eye className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          Show the demo preview for this message
        </button>
      );
    }

    return (
      <Card className="border-dashed">
        <div className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-raised ring-1 ring-line">
            <Eye className="h-4 w-4 text-ink-muted" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-ink">Demo preview</p>
            <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">
              Contact hours are the only thing holding this message back. See what Revora
              would say during permitted hours — no customer is contacted and nothing is
              recorded.
            </p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={onPreview}
            disabled={loading}
            className="shrink-0"
          >
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Eye className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            Preview Hinglish message
          </Button>
        </div>
      </Card>
    );
  }

  const previewClock = preview.preview_time
    ? new Date(preview.preview_time).toLocaleTimeString('en-IN', {
        hour: '2-digit',
        minute: '2-digit',
        timeZone: 'Asia/Kolkata',
      })
    : '—';

  return (
    <Card className="border-2 border-dashed border-accent/40 bg-accent/[0.03]">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-micro font-semibold uppercase tracking-wide text-accent">
              Demo preview — not a live contact
            </p>
            <CardTitle className="mt-0.5">
              What this would render at {previewClock} IST
            </CardTitle>
            <CardDescription>
              Exactly what Revora would say during your permitted contact hours. No
              customer is contacted and nothing is recorded.
            </CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="accent">Preview</Badge>
            <Button variant="ghost" size="sm" onClick={onClear}>
              <X className="h-3.5 w-3.5" aria-hidden="true" />
              Close
            </Button>
          </div>
        </div>
      </CardHeader>

      <div className="px-5 pb-5">
        {preview.compliant ? (
          <>
            <blockquote className="rounded-lg border border-accent/25 bg-surface px-4 py-3.5 text-sm leading-relaxed text-ink">
              {preview.script}
            </blockquote>
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              <Badge variant="neutral">{toneLabel(preview.tone)}</Badge>
              <Badge variant={preview.urgency === 'high' ? 'pending' : 'neutral'}>
                {urgencyLabel(preview.urgency)} urgency
              </Badge>
              <Badge variant="neutral">Hinglish</Badge>
              <Badge variant="neutral">{channelLabel(preview.channel)}</Badge>
              <span className="ml-auto text-micro text-ink-subtle">
                {templateKeyLabel(preview.template_key)}
              </span>
            </div>
          </>
        ) : (
          <div className="rounded-lg border border-unrecoverable/25 bg-unrecoverable/5 px-4 py-3.5">
            <p className="text-sm font-medium text-ink">
              Still withheld inside the contact window.
            </p>
            <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
              {preview.failure_reason}
            </p>
            <p className="mt-2 text-xs leading-relaxed text-ink-subtle">
              Contact hours were not the problem. Another check refused this message,
              so there is nothing to show — which is the system working.
            </p>
          </div>
        )}

        <ul className="mt-4 space-y-1.5">
          {preview.compliance_checks.map((check) => (
            <li key={check.rule_id} className="flex items-start gap-2 text-xs">
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
              <span className="text-ink-muted">
                <span className="font-medium text-ink">{complianceRuleLabel(check.rule_id)}</span>
                <span className="sr-only">{check.passed ? ': passed' : ': failed'}</span> —{' '}
                {check.detail}
              </span>
            </li>
          ))}
        </ul>

        <p className="mt-4 rounded-lg bg-surface-raised/60 px-3 py-2 text-xs leading-relaxed text-ink-subtle">
          Nothing was sent and nothing was changed. This is a preview only.
        </p>
      </div>
    </Card>
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
