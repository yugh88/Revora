'use client';

import * as React from 'react';
import { Building2, CheckCircle2, ShieldCheck } from 'lucide-react';

import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { AppShell } from '../../components/ui/site-header';
import { cn } from '../../components/ui/utils';

/**
 * Settings.
 *
 * Deliberately light. There is no login, no password and no user table, because
 * this build does not need one and enterprise authentication added "for
 * completeness" is a large attack surface bought with no benefit.
 *
 * Preferences are kept in the browser. They shape how this installation
 * presents itself; nothing here can change what the recovery engine does. The
 * limits that actually bind Revora — attempts, cooldowns, contact caps — live
 * on the Policies page and are enforced server-side, which is where a control
 * over money belongs.
 *
 * No secret appears here. The Razorpay keys live in the backend environment and
 * the frontend has never been able to read them.
 */

interface Preferences {
  merchantName: string;
  businessName: string;
  emailContact: boolean;
  smsContact: boolean;
  voiceContact: boolean;
  notifyRecovered: boolean;
  notifyPromises: boolean;
  notifyAttention: boolean;
}

const DEFAULTS: Preferences = {
  merchantName: 'Yugh Juneja',
  businessName: 'Revora Demo Merchant',
  emailContact: true,
  smsContact: true,
  voiceContact: true,
  notifyRecovered: true,
  notifyPromises: true,
  notifyAttention: true,
};

const STORE = 'revora.preferences';

export default function SettingsPage() {
  const [prefs, setPrefs] = React.useState<Preferences>(DEFAULTS);
  const [saved, setSaved] = React.useState(false);

  React.useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORE);
      if (stored) setPrefs({ ...DEFAULTS, ...(JSON.parse(stored) as Preferences) });
    } catch {
      // A corrupt store just means defaults.
    }
  }, []);

  const save = () => {
    try {
      window.localStorage.setItem(STORE, JSON.stringify(prefs));
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch {
      // Not being able to remember is not worth an error screen.
    }
  };

  const set = <K extends keyof Preferences>(key: K, value: Preferences[K]) =>
    setPrefs((current) => ({ ...current, [key]: value }));

  return (
    <AppShell>
      <main className="mx-auto max-w-[820px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Settings</h1>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
            How Revora presents itself to you. The limits that govern what it may
            actually do live under Policies.
          </p>
        </div>

        <div className="mt-6 space-y-5">
          <Card className="animate-fade-up">
            <CardHeader>
              <div className="flex items-start gap-2.5">
                <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent ring-1 ring-accent/20">
                  <Building2 className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                <div>
                  <CardTitle>Your business</CardTitle>
                  <CardDescription>
                    Used to address you and to sign the report you download.
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <div className="grid grid-cols-1 gap-4 px-5 pb-5 sm:grid-cols-2">
              <Field
                label="Your name"
                value={prefs.merchantName}
                onChange={(value) => set('merchantName', value)}
              />
              <Field
                label="Business name"
                value={prefs.businessName}
                onChange={(value) => set('businessName', value)}
              />
            </div>
          </Card>

          <Card className="animate-fade-up stagger-1">
            <CardHeader>
              <CardTitle>How Revora may reach customers</CardTitle>
              <CardDescription>
                Turning a channel off hides it from this installation. Your policy
                limits still apply on top.
              </CardDescription>
            </CardHeader>
            <div className="space-y-1 px-5 pb-5">
              <Toggle
                label="Email"
                checked={prefs.emailContact}
                onChange={(value) => set('emailContact', value)}
              />
              <Toggle
                label="Text message"
                checked={prefs.smsContact}
                onChange={(value) => set('smsContact', value)}
              />
              <Toggle
                label="Voice call"
                checked={prefs.voiceContact}
                onChange={(value) => set('voiceContact', value)}
              />
            </div>
          </Card>

          <Card className="animate-fade-up stagger-2">
            <CardHeader>
              <CardTitle>What you want to hear about</CardTitle>
              <CardDescription>Which alerts appear in your notifications.</CardDescription>
            </CardHeader>
            <div className="space-y-1 px-5 pb-5">
              <Toggle
                label="Revenue recovered"
                checked={prefs.notifyRecovered}
                onChange={(value) => set('notifyRecovered', value)}
              />
              <Toggle
                label="Promises to pay"
                checked={prefs.notifyPromises}
                onChange={(value) => set('notifyPromises', value)}
              />
              <Toggle
                label="Cases needing attention"
                checked={prefs.notifyAttention}
                onChange={(value) => set('notifyAttention', value)}
              />
            </div>
          </Card>

          <Card className="animate-fade-up stagger-3">
            <CardHeader>
              <div className="flex items-start gap-2.5">
                <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-raised text-ink-muted ring-1 ring-line">
                  <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                <div>
                  <CardTitle>Recovery environment</CardTitle>
                  <CardDescription>Where recovery actions are carried out.</CardDescription>
                </div>
              </div>
            </CardHeader>
            <div className="px-5 pb-5">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="accent">Demo Simulation</Badge>
                <span className="text-xs text-ink-muted">
                  Safe demo environment. No real payments or customer contacts.
                </span>
              </div>
              <p className="mt-3 text-xs leading-relaxed text-ink-subtle">
                Razorpay Test Sandbox can be chosen per run on the Run Recovery page. It
                is sandbox-only and never touches production. Your Razorpay keys are held
                by the server and are never sent to this page.
              </p>
            </div>
          </Card>

          <div className="flex items-center gap-3">
            <Button onClick={save}>Save preferences</Button>
            {saved ? (
              <span className="inline-flex items-center gap-1.5 text-xs text-recovered">
                <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                Saved
              </span>
            ) : null}
          </div>
        </div>
      </main>
    </AppShell>
  );
}

function Field({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="text-micro uppercase text-ink-subtle">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1.5 h-9 w-full rounded-lg border border-line bg-surface px-3 text-xs text-ink outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
      />
    </label>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-4 rounded-lg px-2 py-2 transition-colors hover:bg-surface-raised">
      <span className="text-sm text-ink">{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative h-5 w-9 shrink-0 rounded-full transition-colors outline-none',
          'focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg',
          checked ? 'bg-accent' : 'bg-line-strong',
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            'absolute top-0.5 h-4 w-4 rounded-full bg-surface transition-transform',
            checked ? 'translate-x-[1.15rem]' : 'translate-x-0.5',
          )}
        />
      </button>
    </label>
  );
}
