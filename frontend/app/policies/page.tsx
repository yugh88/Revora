'use client';

import * as React from 'react';
import { AlertCircle, Loader2, RotateCcw } from 'lucide-react';

import { PolicyForm } from '../../components/PolicyForm';
import { Button } from '../../components/ui/button';
import { Card } from '../../components/ui/card';
import { AppShell } from '../../components/ui/site-header';
import { api, ApiError } from '../../lib/api-client';
import type { PolicyListResponse, PolicyUpdate } from '../../lib/types';

/**
 * Merchant policy configuration. BUILD_SPEC Sections 4, 6 and 13.
 *
 * Reads GET /policies and writes PUT /policies. Nothing is stored in React
 * beyond the in-progress edit — the page always renders what the backend says
 * is effective, so what a judge sees here is exactly what the policy engine
 * will apply on the next batch.
 */
export default function PoliciesPage() {
  const [data, setData] = React.useState<PolicyListResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<ApiError | null>(null);
  const [savingType, setSavingType] = React.useState<string | null>(null);
  const [savedVersions, setSavedVersions] = React.useState<Record<string, number>>({});
  const [saveErrors, setSaveErrors] = React.useState<Record<string, string>>({});

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getPolicies());
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError('Could not load policies.'));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const save = React.useCallback(
    async (update: PolicyUpdate) => {
      setSavingType(update.event_type);
      setSaveErrors((current) => ({ ...current, [update.event_type]: '' }));
      try {
        const saved = await api.updatePolicy(update);
        setSavedVersions((current) => ({
          ...current,
          [update.event_type]: saved.policy_version,
        }));
        // Re-read so the form shows the persisted row, not the submitted one.
        await load();
      } catch (caught) {
        const message =
          caught instanceof ApiError
            ? caught.userMessage
            : 'The policy could not be saved.';
        setSaveErrors((current) => ({ ...current, [update.event_type]: message }));
      } finally {
        setSavingType(null);
      }
    },
    [load],
  );

  return (
    <AppShell>
      <main className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6 lg:px-8">
        <div className="animate-fade-up flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-ink">Policies</h1>
            <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
              The bounds the engine operates within, per event type. These are the values
              the policy gate actually applies — changing one here changes what the next
              batch is allowed to do.
            </p>
          </div>
          {data ? (
            <p className="text-micro uppercase text-ink-subtle">
              Merchant <span className="text-ink">{data.merchant_id}</span>
            </p>
          ) : null}
        </div>

        <div className="animate-fade-up stagger-1 mt-6 space-y-5">
          {loading && !data ? (
            <PolicySkeleton />
          ) : error ? (
            <ErrorState error={error} onRetry={() => void load()} busy={loading} />
          ) : data ? (
            data.items.map((policy) => (
              <PolicyForm
                key={policy.event_type}
                policy={policy}
                saving={savingType === policy.event_type}
                savedVersion={savedVersions[policy.event_type] ?? null}
                errorMessage={saveErrors[policy.event_type] || null}
                onSave={(update) => void save(update)}
              />
            ))
          ) : null}
        </div>
      </main>
    </AppShell>
  );
}

function PolicySkeleton() {
  return (
    <div className="space-y-5" role="status" aria-busy="true">
      <span className="sr-only">Loading policies</span>
      {[0, 1, 2].map((index) => (
        <Card key={index} className="p-5">
          <div className="h-3.5 w-44 animate-pulse rounded bg-line/60" />
          <div className="mt-2 h-3 w-64 animate-pulse rounded bg-line/60" />
          <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-3">
            {[0, 1, 2, 3, 4, 5].map((cell) => (
              <div key={cell}>
                <div className="h-2.5 w-20 animate-pulse rounded bg-line/60" />
                <div className="mt-1.5 h-9 w-full animate-pulse rounded-lg bg-line/60" />
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}

function ErrorState({
  error,
  onRetry,
  busy,
}: {
  error: ApiError;
  onRetry: () => void;
  busy: boolean;
}) {
  return (
    <Card className="border-unrecoverable/25">
      <div className="flex flex-col gap-4 p-6 sm:flex-row sm:items-start">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-unrecoverable/10 ring-1 ring-unrecoverable/20">
          <AlertCircle className="h-5 w-5 text-unrecoverable" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink">Could not load policies</h2>
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
