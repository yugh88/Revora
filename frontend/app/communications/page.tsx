'use client';

import * as React from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Loader2,
  Mail,
  MessageSquare,
  PhoneCall,
  RotateCcw,
  Play,
  Send,
  Square,
  Volume2,
  Eye,
  ShieldX,
  Smartphone,
  X,
} from 'lucide-react';

import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { AppShell } from '../../components/ui/site-header';
import { LiveIndicator } from '../../components/ui/live-status';
import { useLiveRefresh } from '../../components/ui/use-live-data';
import { cn } from '../../components/ui/utils';
import {
  api,
  ApiError,
  formatDateTime,
  formatInr,
  formatRelative,
} from '../../lib/api-client';
import {
  communicationNextStep,
  communicationStatusLabel,
  communicationStatusMeaning,
  contactLabel,
  customerResponseLabel,
  rootCauseLabel,
} from '../../lib/labels';
import type {
  CommunicationListResponse,
  CommunicationOut,
  EventSummary,
  ScriptResponse,
} from '../../lib/types';

/**
 * Recovery communications — Email, SMS and Voice.
 *
 * This is the conversation half of recovery: Revora decides someone should be
 * contacted, writes the message, and this is where a merchant sees what it
 * would say and what came of it.
 *
 * NOTHING IS SENT FROM HERE. There is no email, SMS or voice provider behind
 * any of it, so every action is labelled a simulation and the word "sent"
 * appears only as "Demo sent". A judge should never be able to mistake a
 * represented message for a delivered one.
 *
 * A simulated customer reply of "I'll pay by..." creates a real Promise to Pay
 * on the same case, which is how a promise comes to exist as a consequence of
 * the conversation rather than something typed in on the customer's behalf.
 */

const CHANNELS = [
  { value: '', label: 'All channels', icon: MessageSquare },
  { value: 'email', label: 'Email', icon: Mail },
  { value: 'sms', label: 'SMS', icon: Smartphone },
  { value: 'voice_script', label: 'Voice', icon: PhoneCall },
] as const;

const CHANNEL_ICON: Record<string, typeof Mail> = {
  email: Mail,
  sms: Smartphone,
  voice_script: PhoneCall,
  in_app: MessageSquare,
};

/** Time windows for the contact history, in days. */
const HISTORY_WINDOWS = [
  { value: '1', label: 'Last day' },
  { value: '7', label: 'Last week' },
  { value: '30', label: 'Last month' },
  { value: '90', label: 'Last 3 months' },
  { value: '180', label: 'Last 6 months' },
  { value: '365', label: 'Last 12 months' },
  { value: '', label: 'All time' },
] as const;

/** Turn a window into the instant the API filters on. */
function sinceIso(days: string): string | undefined {
  if (!days) return undefined;
  const from = new Date();
  from.setDate(from.getDate() - Number.parseInt(days, 10));
  return from.toISOString();
}

const STATUS_TONE: Record<string, React.ComponentProps<typeof Badge>['variant']> = {
  prepared: 'neutral',
  simulated: 'accent',
  blocked: 'unrecoverable',
};

export default function CommunicationsPage() {
  return (
    <React.Suspense fallback={null}>
      <Communications />
    </React.Suspense>
  );
}

function Communications() {
  const [data, setData] = React.useState<CommunicationListResponse | null>(null);
  const [since, setSince] = React.useState<string>('30');
  const [selected, setSelected] = React.useState<CommunicationOut | null>(null);
  // The sidebar deep-links with ?channel=, and the API applies that filter
  // server-side — which is what makes those sub-items real navigation.
  const searchParams = useSearchParams();
  const urlChannel = searchParams.get('channel') ?? '';
  const [channel, setChannel] = React.useState(urlChannel);

  React.useEffect(() => {
    setChannel(urlChannel);
  }, [urlChannel]);

  // A selection that is no longer in view must not linger. Switching from
  // Email to SMS with an email open would otherwise leave that email's detail
  // beside a list it does not belong to.
  React.useEffect(() => {
    setSelected(null);
  }, [channel, since]);
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<ApiError | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);

  const load = React.useCallback(
    async (quiet = false) => {
      if (!quiet) setLoading(true);
      try {
        const body = await api.listCommunications(
          channel || undefined,
          sinceIso(since),
        );
        setData(body);
        // Re-read the open conversation from the fresh list. A reply that
        // arrives while it is on screen should appear in place, and a record
        // that no longer matches the filter should stop being selected.
        setSelected((current) =>
          current ? (body.items.find((c) => c.id === current.id) ?? null) : null,
        );
        setError(null);
        return true;
      } catch (caught) {
        if (!quiet) {
          setError(
            caught instanceof ApiError
              ? caught
              : new ApiError('Recovery messages could not be loaded.'),
          );
        }
        return false;
      } finally {
        setLoading(false);
      }
    },
    [channel, since],
  );

  const { status: liveStatus, lastUpdated } = useLiveRefresh(load, [channel, since]);

  const act = React.useCallback(
    async (fn: () => Promise<unknown>, message: string) => {
      setBusy(true);
      setNotice(null);
      try {
        await fn();
        await load();
        setNotice(message);
      } catch (caught) {
        setError(
          caught instanceof ApiError ? caught : new ApiError('That could not be completed.'),
        );
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const items = data?.items ?? [];

  return (
    <AppShell>
      <main className="mx-auto max-w-[1240px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight text-ink">
              Recovery communications
            </h1>
            <LiveIndicator status={liveStatus} lastUpdated={lastUpdated} />
          </div>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
            The customers Revora decided to contact, why it chose them, and what came
            of each conversation.
          </p>
        </div>

        {notice ? (
          <p className="animate-fade-up mt-4 flex items-center gap-2 rounded-lg border border-accent/25 bg-accent/5 px-3.5 py-2.5 text-xs text-ink-muted">
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-accent" aria-hidden="true" />
            {notice}
          </p>
        ) : null}

        <div className="animate-fade-up stagger-1 mt-5 flex flex-wrap gap-1.5">
          {CHANNELS.map((option) => {
            const Icon = option.icon;
            const active = option.value === channel;
            return (
              <button
                key={option.value || 'all'}
                type="button"
                onClick={() => setChannel(option.value)}
                aria-pressed={active}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors',
                  'outline-none focus-visible:ring-2 focus-visible:ring-accent',
                  active
                    ? 'border-accent/40 bg-accent/[0.07] text-ink'
                    : 'border-line text-ink-subtle hover:border-line-strong hover:text-ink',
                )}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                {option.label}
                {data && option.value && data.channel_breakdown[option.value] ? (
                  <span className="tabular text-ink-subtle">
                    {data.channel_breakdown[option.value]}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>

        <div className="animate-fade-up stagger-1 mt-3 flex flex-wrap items-center gap-2">
          <span className="text-micro uppercase text-ink-subtle">
            Communication history
          </span>
          <label>
            <span className="sr-only">Time period</span>
            <select
              value={since}
              onChange={(event) => setSince(event.target.value)}
              className="h-8 cursor-pointer rounded-lg border border-line bg-surface px-2.5 text-xs text-ink outline-none hover:border-line-strong focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
            >
              {HISTORY_WINDOWS.map((option) => (
                <option key={option.value || 'all'} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          {data ? (
            <span className="tabular text-micro text-ink-subtle">
              {data.total} {data.total === 1 ? 'contact' : 'contacts'}
            </span>
          ) : null}
        </div>

        <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-3">
          <div className="lg:col-span-2">
            {error ? (
              <ErrorState onRetry={() => void load()} busy={loading} />
            ) : loading && !data ? (
              <ListSkeleton />
            ) : items.length === 0 ? (
              <EmptyState />
            ) : (
              <Card className="overflow-hidden">
                <ul className="divide-y divide-line">
                  {items.map((item) => {
                    const Icon = CHANNEL_ICON[item.channel] ?? MessageSquare;
                    return (
                      <li key={item.id}>
                        <button
                          type="button"
                          onClick={() => setSelected(item)}
                          aria-current={selected?.id === item.id ? 'true' : undefined}
                          className={cn(
                            'block w-full px-4 py-3.5 text-left outline-none transition-colors',
                            'focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent',
                            selected?.id === item.id
                              ? 'bg-accent/[0.06]'
                              : 'hover:bg-surface-raised/70',
                          )}
                        >
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                            <Icon
                              className="h-3.5 w-3.5 shrink-0 text-ink-subtle"
                              aria-hidden="true"
                            />
                            <span className="text-sm font-medium text-ink">
                              {item.customer_name}
                            </span>
                            <Badge variant={STATUS_TONE[item.status] ?? 'neutral'}>
                              {communicationStatusLabel(item.status)}
                            </Badge>
                            <span
                              className="tabular ml-auto text-xs text-ink-subtle"
                              title={formatDateTime(item.created_at)}
                            >
                              {formatRelative(item.created_at)}
                            </span>
                          </div>
                          <p className="mt-1 text-xs text-ink-subtle">
                            {contactLabel(item.channel)} · {rootCauseLabel(item.reason)} ·{' '}
                            {item.status === 'blocked'
                              ? 'Policy blocked this'
                              : 'Policy allowed this'}
                          </p>
                          {item.customer_response ? (
                            <p className="mt-1 text-micro text-accent">
                              {customerResponseLabel(item.customer_response)}
                            </p>
                          ) : null}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </Card>
            )}
          </div>

          <div className="lg:col-span-1">
            {selected ? (
              <ContactDetail
                // The fix for stale detail state. Without a key React reuses
                // this component instance across selections, so customer A's
                // fetched preview and chosen date survive into customer B's
                // panel. Keying on the record id forces a fresh instance, which
                // is the difference between "the same component showing new
                // props" and "a new component".
                key={selected.id}
                contact={selected}
                busy={busy}
                onSend={() =>
                  void act(
                    () => api.simulateSend(selected.id),
                    'Message simulated. No customer was contacted.',
                  )
                }
                onRespond={(body, message) =>
                  void act(() => api.simulateResponse(selected.id, body), message)
                }
                onClose={() => setSelected(null)}
              />
            ) : (
              <AgentSummary data={data} />
            )}
          </div>
        </div>
      </main>
    </AppShell>
  );
}

/* -------------------------------------------------------------------------- */

function ContactDetail({
  contact,
  busy,
  onSend,
  onRespond,
  onClose,
}: {
  contact: CommunicationOut;
  busy: boolean;
  onSend: () => void;
  onClose: () => void;
  onRespond: (
    body: { response: string; promised_amount?: string; promised_date?: string },
    message: string,
  ) => void;
}) {
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 3);
  const [date, setDate] = React.useState(tomorrow.toISOString().slice(0, 10));

  return (
    <Card className="sticky top-24">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle>{contactLabel(contact.channel)}</CardTitle>
            <CardDescription>
              {contact.customer_name} · {rootCauseLabel(contact.reason)}
            </CardDescription>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close">
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </div>
      </CardHeader>

      <div className="px-5 pb-5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={STATUS_TONE[contact.status] ?? 'neutral'}>
            {communicationStatusLabel(contact.status)}
          </Badge>
          {contact.is_simulated ? <Badge variant="neutral">Demo</Badge> : null}
        </div>
        <p className="mt-2 text-xs leading-relaxed text-ink-muted">
          {communicationStatusMeaning(contact.status)}
        </p>

        {contact.channel === 'voice_script' && contact.body ? (
          <VoiceDemo script={contact.body} />
        ) : null}

        {contact.channel_reason ? (
          <p className="mt-2 rounded-lg bg-surface-raised/60 px-3 py-2 text-xs leading-relaxed text-ink-muted">
            <span className="font-medium text-ink">Why this channel: </span>
            {contact.channel_reason}
          </p>
        ) : null}

        {contact.status === 'blocked' ? (
          <>
            <div className="mt-3 rounded-lg border border-unrecoverable/25 bg-unrecoverable/5 px-3 py-2.5">
              <p className="text-xs font-medium text-ink">No message was written.</p>
              <p className="mt-1 text-xs leading-relaxed text-ink-muted">
                {contact.blocked_reason}
              </p>
            </div>
            {/* Held back only by contact hours? Then the same message would be
                written during permitted hours, and a judge arriving in the
                evening should be able to see it. Reuses the existing read-only
                preview: it moves the clock for the contact-hours check alone,
                runs every other rule for real, and writes nothing. */}
            {blockedOnlyByHours(contact.blocked_reason) ? (
              <BlockedPreview eventId={contact.event_id} />
            ) : null}
          </>
        ) : (
          <blockquote className="relative mt-3 rounded-lg border border-line bg-surface-raised/60 px-3.5 py-3 pr-11 text-sm leading-relaxed text-ink">
            {contact.body}
            {contact.channel === 'voice_script' ? (
              <SpeakerButton script={contact.body} />
            ) : null}
          </blockquote>
        )}

        <dl className="mt-4 space-y-2.5 border-t border-line pt-4">
          <Row label="Amount at risk" value={formatInr(contact.amount_at_risk)} />
          <Row label="Prepared" value={formatDateTime(contact.created_at)} />
          {contact.simulated_at ? (
            <Row label="Demo sent" value={formatDateTime(contact.simulated_at)} />
          ) : null}
        </dl>

        <div className="mt-3 rounded-lg bg-surface-raised/50 px-3 py-2">
          <p className="text-micro uppercase text-ink-subtle">Next step</p>
          <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">
            {communicationNextStep(
              contact.status,
              contact.customer_response,
              contact.promise_id,
            )}
          </p>
        </div>

        <div className="mt-4 border-t border-line pt-4">
          <Link
            href={`/events/${contact.event_id}?from=communications`}
            className="inline-flex items-center gap-1 text-xs text-accent outline-none hover:underline focus-visible:ring-2 focus-visible:ring-accent"
          >
            Open the recovery case
            <ArrowRight className="h-3 w-3" aria-hidden="true" />
          </Link>
          {contact.promise_id ? (
            <Link
              href="/promises"
              className="ml-4 inline-flex items-center gap-1 text-xs text-accent outline-none hover:underline focus-visible:ring-2 focus-visible:ring-accent"
            >
              View the promise
              <ArrowRight className="h-3 w-3" aria-hidden="true" />
            </Link>
          ) : null}
        </div>

        {contact.status === 'prepared' ? (
          <DemoBlock>
            <Button size="sm" onClick={onSend} disabled={busy} className="w-full">
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Send className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Simulate sending
            </Button>
          </DemoBlock>
        ) : null}

        {contact.status === 'simulated' && !contact.customer_response ? (
          <DemoBlock>
            <p className="text-xs leading-relaxed text-ink-muted">
              Represent how the customer replied.
            </p>
            <label className="mt-2 block">
              <span className="text-micro uppercase text-ink-subtle">
                If they promise to pay, by when?
              </span>
              <input
                type="date"
                value={date}
                onChange={(event) => setDate(event.target.value)}
                className="tabular mt-1.5 h-9 w-full rounded-lg border border-line bg-surface px-3 text-xs text-ink outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
              />
            </label>
            <div className="mt-2.5 flex flex-col gap-2">
              <Button
                size="sm"
                disabled={busy}
                onClick={() =>
                  onRespond(
                    {
                      response: 'promised_to_pay',
                      promised_date: new Date(`${date}T12:00:00`).toISOString(),
                    },
                    'Customer response simulated. A promise to pay was recorded.',
                  )
                }
              >
                &ldquo;I&rsquo;ll pay by this date&rdquo;
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={busy}
                onClick={() =>
                  onRespond({ response: 'no_response' }, 'Customer response simulated — no reply.')
                }
              >
                No reply
              </Button>
            </div>
          </DemoBlock>
        ) : null}

        {contact.customer_response ? (
          <p className="mt-4 rounded-lg border border-accent/25 bg-accent/5 px-3 py-2 text-xs text-ink-muted">
            {customerResponseLabel(contact.customer_response)}
          </p>
        ) : null}
      </div>
    </Card>
  );
}

/** True when contact hours are the only thing standing in the way. */
function blockedOnlyByHours(reason: string | null): boolean {
  return Boolean(reason && reason.toLowerCase().includes('contact window'));
}

/**
 * What this message would say during permitted hours.
 *
 * Explicitly a preview and explicitly not a contact. It reads through the
 * existing preview endpoint, which evaluates the contact-hours rule against a
 * fixed in-window instant and leaves every other compliance rule running for
 * real — so a message refused for frequency or urgency stays refused here too.
 */
function BlockedPreview({ eventId }: { eventId: string }) {
  const [preview, setPreview] = React.useState<ScriptResponse | null>(null);
  const [loading, setLoading] = React.useState(false);

  if (!preview) {
    return (
      <Button
        variant="secondary"
        size="sm"
        className="mt-3 w-full"
        disabled={loading}
        onClick={() => {
          setLoading(true);
          api
            .previewScript(eventId)
            .then(setPreview)
            .catch(() => undefined)
            .finally(() => setLoading(false));
        }}
      >
        {loading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        ) : (
          <Eye className="h-3.5 w-3.5" aria-hidden="true" />
        )}
        Preview what Revora would say
      </Button>
    );
  }

  return (
    <div className="mt-3 rounded-lg border-2 border-dashed border-accent/40 bg-accent/[0.03] p-3">
      <p className="text-micro font-semibold uppercase tracking-wide text-accent">
        Demo preview — not a live contact
      </p>
      {preview.compliant ? (
        <blockquote className="mt-2 rounded-lg border border-accent/25 bg-surface px-3 py-2.5 text-xs leading-relaxed text-ink">
          {preview.script}
        </blockquote>
      ) : (
        <p className="mt-2 text-xs leading-relaxed text-ink-muted">
          Contact hours were not the only problem — another check refused this message
          too, so there is nothing to show.
        </p>
      )}
      <p className="mt-2 text-micro leading-relaxed text-ink-subtle">
        Nothing was sent and nothing was changed. This is what the message would say
        during your permitted contact hours.
      </p>
    </div>
  );
}

/**
 * Hear the recovery call, as a demonstration.
 *
 * Uses the browser's own speech synthesis. That is a deliberate choice: it
 * needs no provider, no API key, no paid TTS service and no network call, so
 * the demo works on a laptop with the wifi off and cannot accidentally become a
 * dependency on somebody's billing account.
 *
 * No telephone call happens. Nothing is dialled, nothing is recorded, and the
 * label says so — a judge hearing audio must not be able to conclude a customer
 * was rung.
 */
/**
 * A small speaker beside the script itself, so the play control sits where the
 * words are rather than only in a panel further down.
 */
function SpeakerButton({ script }: { script: string }) {
  const [speaking, setSpeaking] = React.useState(false);

  const toggle = () => {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) return;
    if (speaking) {
      window.speechSynthesis.cancel();
      setSpeaking(false);
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(script);
    utterance.lang = 'en-IN';
    utterance.rate = 0.95;
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    setSpeaking(true);
    window.speechSynthesis.speak(utterance);
  };

  return (
    <button
      type="button"
      onClick={toggle}
      title="Voice demo — no call is made"
      aria-label={speaking ? 'Stop the voice demo' : 'Play the voice demo. No call is made.'}
      className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-lg border border-line bg-surface text-ink-muted outline-none transition-colors hover:border-line-strong hover:text-ink focus-visible:ring-2 focus-visible:ring-accent"
    >
      {speaking ? (
        <Square className="h-3 w-3" aria-hidden="true" />
      ) : (
        <Volume2 className="h-3.5 w-3.5" aria-hidden="true" />
      )}
    </button>
  );
}

function VoiceDemo({ script }: { script: string }) {
  const [speaking, setSpeaking] = React.useState(false);
  const [available, setAvailable] = React.useState(false);

  React.useEffect(() => {
    setAvailable(typeof window !== 'undefined' && 'speechSynthesis' in window);
    return () => {
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        window.speechSynthesis.cancel();
      }
    };
  }, []);

  const speak = () => {
    if (!available) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(script);
    // Hinglish read by an Indian English voice where the browser has one.
    utterance.lang = 'en-IN';
    utterance.rate = 0.95;
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    setSpeaking(true);
    window.speechSynthesis.speak(utterance);
  };

  const stop = () => {
    window.speechSynthesis.cancel();
    setSpeaking(false);
  };

  return (
    <div className="mt-3 rounded-lg border border-line bg-surface-raised/50 p-3">
      <p className="text-micro font-semibold uppercase text-ink-subtle">
        Voice demo — no call is made
      </p>
      <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
        Hear how this recovery call would sound. Your browser reads the script
        aloud; nothing is dialled and no customer is contacted.
      </p>
      {available ? (
        <Button
          variant="secondary"
          size="sm"
          className="mt-2.5 w-full"
          onClick={speaking ? stop : speak}
        >
          {speaking ? (
            <>
              <Square className="h-3.5 w-3.5" aria-hidden="true" />
              Stop
            </>
          ) : (
            <>
              <Play className="h-3.5 w-3.5" aria-hidden="true" />
              Play voice demo
            </>
          )}
        </Button>
      ) : (
        <p className="mt-2 text-xs text-ink-subtle">
          This browser cannot read text aloud. The script above is what would be said.
        </p>
      )}
    </div>
  );
}

function DemoBlock({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-4 rounded-lg border border-line bg-surface-raised/50 p-3">
      <p className="text-micro font-semibold uppercase text-ink-subtle">
        Demo controls — simulation only
      </p>
      <div className="mt-2.5">{children}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-ink-subtle">{label}</dt>
      <dd className="tabular text-xs font-medium text-ink">{value}</dd>
    </div>
  );
}

/**
 * What the agent is doing, shown instead of a contact form.
 *
 * The previous panel asked the merchant to pick a customer and pick a channel,
 * which is precisely the job Revora exists to do. Deciding who needs contacting
 * — and how — is the product; asking a person to do it turns an agent into an
 * address book with a send button.
 *
 * Contacts now appear here because a recovery run created them.
 */
function AgentSummary({ data }: { data: CommunicationListResponse | null }) {
  const total = data?.total ?? 0;
  const blocked = data?.status_breakdown?.blocked ?? 0;
  const reached = total - blocked;

  return (
    <Card className="sticky top-24">
      <CardHeader>
        <CardTitle>How Revora decides who to contact</CardTitle>
        <CardDescription>
          You do not pick customers or channels — Revora does, within your policy.
        </CardDescription>
      </CardHeader>

      <div className="px-5 pb-5">
        <ol className="space-y-2">
          {[
            'A recovery run reviews every case at risk',
            'Revora works out which ones a message would actually help',
            'It picks the channel that suits the customer and the situation',
            'Your policy is checked before anything is written',
            'The conversation appears in this list',
          ].map((step, index) => (
            <li key={step} className="flex gap-2.5 text-xs leading-relaxed text-ink-muted">
              <span className="tabular mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-surface-raised text-micro font-semibold text-ink-subtle">
                {index + 1}
              </span>
              {step}
            </li>
          ))}
        </ol>

        {total > 0 ? (
          <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-line pt-4">
            <div>
              <dt className="text-micro uppercase text-ink-subtle">Contacted</dt>
              <dd className="tabular mt-0.5 text-lg font-semibold text-ink">{reached}</dd>
            </div>
            <div>
              <dt className="text-micro uppercase text-ink-subtle">Held by policy</dt>
              <dd
                className={cn(
                  'tabular mt-0.5 text-lg font-semibold',
                  blocked > 0 ? 'text-pending' : 'text-ink',
                )}
              >
                {blocked}
              </dd>
            </div>
          </dl>
        ) : null}

        <Button asChild variant="secondary" className="mt-4 w-full">
          <Link href="/batch">Run a recovery analysis</Link>
        </Button>

        <p className="mt-3 text-xs leading-relaxed text-ink-subtle">
          Simulation only — no customer is contacted.
        </p>
      </div>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */

function ListSkeleton() {
  return (
    <Card className="p-4" role="status" aria-busy="true">
      <span className="sr-only">Loading recovery messages</span>
      <div className="space-y-4">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="space-y-1.5">
            <div className="flex items-center gap-3">
              <div className="h-3.5 w-32 animate-pulse rounded bg-line/60" />
              <div className="h-4 w-20 animate-pulse rounded-full bg-line/60" />
              <div className="ml-auto h-3.5 w-24 animate-pulse rounded bg-line/60" />
            </div>
            <div className="h-3 w-56 animate-pulse rounded bg-line/60" />
          </div>
        ))}
      </div>
    </Card>
  );
}

function EmptyState() {
  return (
    <Card className="flex flex-col items-center px-6 py-16 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-line bg-surface-raised">
        <Mail className="h-5 w-5 text-ink-subtle" aria-hidden="true" />
      </span>
      <h2 className="mt-4 text-base font-semibold text-ink">
        No conversations in this period
      </h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-ink-muted">
        Run a recovery analysis and the customers Revora decides to contact will appear
        here, with the message it would send and whether your policy allowed it.
      </p>
    </Card>
  );
}

function ErrorState({ onRetry, busy }: { onRetry: () => void; busy: boolean }) {
  return (
    <Card className="border-unrecoverable/25">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-start">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-unrecoverable/10 ring-1 ring-unrecoverable/20">
          <AlertCircle className="h-5 w-5 text-unrecoverable" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink">
            Recovery messages could not be loaded
          </h2>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
            Nothing was changed and nothing was sent. This was a problem reading your
            messages.
          </p>
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
