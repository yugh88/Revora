'use client';

import * as React from 'react';
import { AlertCircle, CalendarRange, Download, Loader2, X } from 'lucide-react';

import { api, ApiError } from '../lib/api-client';
import { Button } from './ui/button';
import { cn } from './ui/utils';

/**
 * Download a report for a chosen period.
 *
 * One component, used by both Audit and All Recoveries. The two tabs report on
 * different things but the choice a person makes is identical — which period —
 * so duplicating this would mean two dialogs drifting apart.
 *
 * The PDF is built by the backend from the same rows the page is showing, so
 * the document and the screen agree by construction. Nothing is computed here.
 */

export type ReportKind = 'recovery' | 'audit';

const PRESETS: Array<{ id: string; label: string; days?: number }> = [
  { id: 'day', label: 'Last 1 day', days: 1 },
  { id: 'week', label: 'Last 1 week', days: 7 },
  { id: 'month', label: 'Last 1 month', days: 30 },
  { id: 'all', label: 'All time' },
  { id: 'custom', label: 'Custom range' },
];

/**
 * A picked calendar date, as an instant — without moving the date.
 *
 * `new Date('2026-08-01T00:00:00')` is parsed as LOCAL time, so in IST it
 * becomes 2026-07-31T18:30Z: the report silently starts a day early, and the
 * end bound loses the last five and a half hours of the final day.
 *
 * A date input yields a calendar date, not an instant, so the bounds are built
 * directly as UTC. The backend already reads bounds as UTC, so the day a
 * merchant picks is the day they get — in every timezone.
 */
function dayStart(day: string): string {
  return `${day}T00:00:00.000Z`;
}

function dayEnd(day: string): string {
  return `${day}T23:59:59.999Z`;
}

/** Today, as the value a date input expects. */
function isoDay(offsetDays = 0): string {
  const day = new Date();
  day.setDate(day.getDate() - offsetDays);
  return day.toISOString().slice(0, 10);
}

export function ReportDialog({
  kind,
  title,
  open,
  onClose,
}: {
  kind: ReportKind;
  /** What the report covers, in the merchant's words. */
  title: string;
  open: boolean;
  onClose: () => void;
}) {
  const [preset, setPreset] = React.useState('month');
  const [from, setFrom] = React.useState(isoDay(30));
  const [to, setTo] = React.useState(isoDay(0));
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  // Reopening should offer a clean choice, not the last attempt's error.
  React.useEffect(() => {
    if (open) setError(null);
  }, [open]);

  // Escape closes, as it does in every dialog a person has used.
  React.useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  const custom = preset === 'custom';
  const rangeInvalid = custom && from > to;

  const download = async () => {
    if (rangeInvalid) return;
    setBusy(true);
    setError(null);
    try {
      const chosen = PRESETS.find((option) => option.id === preset);
      const blob = await api.downloadReport(
        kind,
        custom ? { from: dayStart(from), to: dayEnd(to) } : { days: chosen?.days },
      );

      // Saved via an object URL and revoked immediately: leaving it alive
      // holds the whole PDF in memory for the life of the tab.
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `revora-${kind}-${isoDay(0)}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      onClose();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? 'The report could not be generated. Nothing was changed — try a shorter period.'
          : 'The report could not be downloaded.',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Close"
        onClick={() => !busy && onClose()}
        className="absolute inset-0 cursor-default bg-ink/25 backdrop-blur-[2px]"
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="report-dialog-title"
        className="animate-fade-up relative w-full max-w-md overflow-hidden rounded-card border border-line bg-surface shadow-card-hover"
      >
        <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="flex items-start gap-2.5">
            <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent ring-1 ring-accent/20">
              <CalendarRange className="h-3.5 w-3.5" aria-hidden="true" />
            </span>
            <div>
              <h2 id="report-dialog-title" className="text-sm font-semibold text-ink">
                Download report
              </h2>
              <p className="mt-0.5 text-xs text-ink-muted">{title}</p>
            </div>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </div>

        <div className="px-5 py-4">
          <fieldset>
            <legend className="text-micro uppercase tracking-wide text-ink-subtle">
              Period
            </legend>
            <div role="radiogroup" aria-label="Period" className="mt-2 flex flex-wrap gap-1.5">
              {PRESETS.map((option) => {
                const active = option.id === preset;
                return (
                  <button
                    key={option.id}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    disabled={busy}
                    onClick={() => setPreset(option.id)}
                    className={cn(
                      'rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors',
                      'outline-none focus-visible:ring-2 focus-visible:ring-accent',
                      'disabled:cursor-not-allowed disabled:opacity-60',
                      active
                        ? 'border-accent/40 bg-accent/[0.07] text-ink'
                        : 'border-line text-ink-subtle hover:border-line-strong hover:text-ink',
                    )}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </fieldset>

          {custom ? (
            <div className="mt-3 grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-micro uppercase text-ink-subtle">From</span>
                <input
                  type="date"
                  value={from}
                  max={to}
                  disabled={busy}
                  onChange={(event) => setFrom(event.target.value)}
                  className="tabular mt-1.5 h-9 w-full rounded-lg border border-line bg-surface px-3 text-xs text-ink outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
                />
              </label>
              <label className="block">
                <span className="text-micro uppercase text-ink-subtle">To</span>
                <input
                  type="date"
                  value={to}
                  min={from}
                  disabled={busy}
                  onChange={(event) => setTo(event.target.value)}
                  className="tabular mt-1.5 h-9 w-full rounded-lg border border-line bg-surface px-3 text-xs text-ink outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
                />
              </label>
            </div>
          ) : null}

          {rangeInvalid ? (
            <p className="mt-2.5 text-xs text-unrecoverable">
              The start date is after the end date.
            </p>
          ) : null}

          {error ? (
            <p className="mt-3 flex items-start gap-2 rounded-lg border border-unrecoverable/25 bg-unrecoverable/5 px-3 py-2 text-xs leading-relaxed text-ink-muted">
              <AlertCircle
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-unrecoverable"
                aria-hidden="true"
              />
              {error}
            </p>
          ) : null}

          <p className="mt-3 text-xs leading-relaxed text-ink-subtle">
            The report covers everything recorded in the period — decisions, the
            reasoning behind them, policy checks, communications, promises and
            outcomes.
          </p>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line px-5 py-3.5">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button size="sm" onClick={() => void download()} disabled={busy || rangeInvalid}>
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {busy ? 'Preparing…' : 'Download PDF'}
          </Button>
        </div>
      </div>
    </div>
  );
}
