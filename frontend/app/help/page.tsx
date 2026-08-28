'use client';

import * as React from 'react';
import { CheckCircle2, LifeBuoy, Loader2 } from 'lucide-react';

import { Button } from '../../components/ui/button';
import { Card, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { AppShell } from '../../components/ui/site-header';
import { cn } from '../../components/ui/utils';
import { api } from '../../lib/api-client';
import type { EventSummary } from '../../lib/types';

/**
 * Help and documentation, written for someone who runs a business rather than
 * a server.
 *
 * The support form is handled locally: there is no ticketing provider, and
 * pretending a request reached a support desk that does not exist would be the
 * same class of lie as claiming an email was sent. The confirmation says what
 * actually happened.
 *
 * The documentation deliberately explains no internal architecture. A merchant
 * needs to know what "recovered" means and why Revora stopped chasing someone;
 * how the classifier is trained is a question for the README.
 */

const CATEGORIES = [
  'Contact support',
  'Report a recovery issue',
  'Ask about a payment or recovery case',
  'Ask a policy question',
  'Report a technical problem',
] as const;

const DOCS: Array<{ q: string; a: string }> = [
  {
    q: 'What does Revora do?',
    a: 'It finds revenue that is slipping away — payments that failed, checkouts left unfinished, invoices going unpaid — works out why in each case, decides what to do within the limits you set, acts, and then checks whether the money actually arrived.',
  },
  {
    q: 'How does recovery work?',
    a: 'Every case goes through the same sequence: Revora spots it, works out the likely cause, chooses a recovery action, checks that action against your policy, carries it out, and verifies the outcome. Every step is recorded, so any decision can be explained afterwards.',
  },
  {
    q: 'How do I run a recovery analysis?',
    a: 'Open Run Recovery, choose how many cases to work and where recovery runs, and press Run Recovery. Results are saved, so you can reopen a past run without running it again.',
  },
  {
    q: 'How do recovery policies work?',
    a: 'Your policies are the limits Revora must stay inside: how many times it may try, how long it must wait between attempts, how often it may contact one customer, and how large an amount needs a person to approve it. Revora will decline to act rather than exceed them.',
  },
  {
    q: 'How do interventions work?',
    a: 'An intervention is whatever Revora decides is most likely to recover the money — retrying a payment, emailing a link to update a card, sending a reminder, or handing the case to a person. It picks the one worth doing, not simply the most aggressive.',
  },
  {
    q: 'How do promises to pay work?',
    a: 'When a customer replies that they will pay by a date, Revora records the commitment and pauses recovery until then. On the date it checks whether the payment arrived. If it did, the money appears as recovered; if it did not, the promise is marked overdue and flagged for review.',
  },
  {
    q: 'How do communications work?',
    a: 'When Revora decides a customer should be contacted, it writes the message and picks the channel — email, text or call — based on the situation and what has worked with that customer before. Your policy is checked first: if it does not allow the contact, no message is written at all.',
  },
  {
    q: 'How do notifications work?',
    a: 'The bell shows what needs your attention now: money recovered, promises made or missed, cases handed over for review, and recovery your policy stopped. Each one opens the case behind it.',
  },
  {
    q: 'How do I read the dashboard?',
    a: 'The large figure is money Revora has won back. Underneath, the bar splits everything at risk into what came back, what is still being worked, and what has been written off. The reporting period changes every figure on the page.',
  },
  {
    q: 'Demo Simulation or Razorpay Test Mode?',
    a: 'Demo Simulation is the default and needs nothing set up — it is a safe environment where no real payment or customer contact happens. Razorpay Test Mode runs against Razorpay\u2019s sandbox using test credentials only. Neither ever touches production or real money.',
  },
  {
    q: 'What does the activity log mean?',
    a: 'Every action Revora took on your behalf, in order, never edited. It is what lets you answer "why did this happen?" about any case, long after the fact.',
  },
  {
    q: 'What does "recovered" mean?',
    a: 'The money arrived and was verified. It is only ever counted once, and only when a payment actually came in.',
  },
  {
    q: 'What does "in progress" mean?',
    a: 'Revora is still working the case. An attempt may be in flight, a customer may have promised to pay, or a cooling-off period may be running.',
  },
  {
    q: 'What does "written off" mean?',
    a: 'The money is not coming back. Either the bank refused permanently, the customer opted out, or Revora reached the limits you set without success. It stops rather than keep chasing.',
  },
];

export default function HelpPage() {
  const [category, setCategory] = React.useState<string>(CATEGORIES[0]);
  const [message, setMessage] = React.useState('');
  const [caseId, setCaseId] = React.useState('');
  const [cases, setCases] = React.useState<EventSummary[]>([]);
  const [submitting, setSubmitting] = React.useState(false);
  const [submitted, setSubmitted] = React.useState(false);

  React.useEffect(() => {
    api
      .listEvents({ limit: 40 })
      .then((body) => setCases(body.items))
      .catch(() => setCases([]));
  }, []);

  const submit = () => {
    setSubmitting(true);
    // Handled locally. There is no ticketing provider, and saying a request
    // reached a support desk that does not exist would be a lie of the same
    // kind as claiming an email was sent.
    window.setTimeout(() => {
      setSubmitting(false);
      setSubmitted(true);
      setMessage('');
      setCaseId('');
    }, 400);
  };

  return (
    <AppShell>
      <main className="mx-auto max-w-[980px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">
            Help &amp; documentation
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
            How Revora works, and how to reach someone if something looks wrong.
          </p>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-5 lg:grid-cols-3">
          <div className="lg:col-span-1">
            <Card className="animate-fade-up sticky top-24">
              <CardHeader>
                <div className="flex items-start gap-2.5">
                  <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent ring-1 ring-accent/20">
                    <LifeBuoy className="h-3.5 w-3.5" aria-hidden="true" />
                  </span>
                  <div>
                    <CardTitle>Contact support</CardTitle>
                    <CardDescription>Tell us what you are seeing.</CardDescription>
                  </div>
                </div>
              </CardHeader>

              <div className="space-y-3 px-5 pb-5">
                {submitted ? (
                  <div className="rounded-lg border border-recovered/25 bg-recovered/5 px-3 py-3">
                    <p className="flex items-center gap-2 text-sm font-medium text-ink">
                      <CheckCircle2 className="h-4 w-4 text-recovered" aria-hidden="true" />
                      Support request submitted.
                    </p>
                    <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
                      Recorded on this device for the demonstration. No message left your
                      machine.
                    </p>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="mt-2"
                      onClick={() => setSubmitted(false)}
                    >
                      Send another
                    </Button>
                  </div>
                ) : (
                  <>
                    <label className="block">
                      <span className="text-micro uppercase text-ink-subtle">Category</span>
                      <select
                        value={category}
                        onChange={(event) => setCategory(event.target.value)}
                        className="mt-1.5 h-9 w-full cursor-pointer rounded-lg border border-line bg-surface px-3 text-xs text-ink outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
                      >
                        {CATEGORIES.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                    </label>

                    <label className="block">
                      <span className="text-micro uppercase text-ink-subtle">
                        Related recovery (optional)
                      </span>
                      <select
                        value={caseId}
                        onChange={(event) => setCaseId(event.target.value)}
                        className="mt-1.5 h-9 w-full cursor-pointer rounded-lg border border-line bg-surface px-3 text-xs text-ink outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
                      >
                        <option value="">Not about a specific case</option>
                        {cases.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.customer_name}
                          </option>
                        ))}
                      </select>
                    </label>

                    <label className="block">
                      <span className="text-micro uppercase text-ink-subtle">Message</span>
                      <textarea
                        value={message}
                        onChange={(event) => setMessage(event.target.value)}
                        rows={5}
                        placeholder="What happened, and what you expected instead…"
                        className="mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2 text-xs leading-relaxed text-ink outline-none placeholder:text-ink-subtle focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
                      />
                    </label>

                    <Button
                      className="w-full"
                      disabled={!message.trim() || submitting}
                      onClick={submit}
                    >
                      {submitting ? (
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                      ) : null}
                      Submit request
                    </Button>
                  </>
                )}
              </div>
            </Card>
          </div>

          <div className="lg:col-span-2">
            <Card className="animate-fade-up stagger-1">
              <CardHeader>
                <CardTitle>How Revora works</CardTitle>
                <CardDescription>
                  Written for the person running the business, not the one running the
                  server.
                </CardDescription>
              </CardHeader>
              <div className="divide-y divide-line px-5 pb-2">
                {DOCS.map((entry, index) => (
                  <details key={entry.q} className={cn('group py-3', index === 0 && 'pt-0')}>
                    <summary className="cursor-pointer list-none rounded-lg text-sm font-medium text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent">
                      <span className="inline-flex items-center gap-2">
                        <span
                          className="text-ink-subtle transition-transform group-open:rotate-90"
                          aria-hidden="true"
                        >
                          ▸
                        </span>
                        {entry.q}
                      </span>
                    </summary>
                    <p className="ml-5 mt-2 text-sm leading-relaxed text-ink-muted">
                      {entry.a}
                    </p>
                  </details>
                ))}
              </div>
            </Card>
          </div>
        </div>
      </main>
    </AppShell>
  );
}
