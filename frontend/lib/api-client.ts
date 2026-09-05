/**
 * Typed client for the Revora FastAPI backend.
 *
 * One place that knows how to talk to the API: one base URL, one error shape,
 * one timeout policy. Components call `api.runBatch(...)` and never touch
 * `fetch`, so there is no per-component error handling to drift out of sync.
 *
 * No secret ever appears here. The browser bundle is public; the Razorpay keys
 * live in the backend's `.env` and the frontend never sees them.
 */

import type {
  AuditListResponse,
  AuditQuery,
  BatchRequest,
  BatchResponse,
  EventDetailResponse,
  EventListQuery,
  EventListResponse,
  EventType,
  HealthResponse,
  CommunicationListResponse,
  CommunicationOut,
  DryRunRequest,
  DryRunResponse,
  NotificationListResponse,
  PromiseCreate,
  PromiseListResponse,
  PromiseOut,
  RunDetailResponse,
  RunListResponse,
  PolicyListResponse,
  PolicyUpdate,
  PolicyOut,
  ScriptResponse,
} from './types';

/**
 * Where the backend actually lives. Display only — shown in the connectivity
 * indicator and in error messages so a misconfiguration is diagnosable.
 * Configured through NEXT_PUBLIC_API_URL, defaulting to the local uvicorn port
 * so a fresh clone runs with no setup. Never a hardcoded production host.
 */
export const BACKEND_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
).replace(/\/+$/, '');

/**
 * What fetch actually calls: a SAME-ORIGIN path, rewritten to BACKEND_URL by
 * Next (see next.config.js).
 *
 * The backend ships no CORS middleware and is frozen, so a direct cross-origin
 * call from the browser would be blocked before it left the page. Routing
 * through Next's rewrite keeps every request same-origin and needs no backend
 * change.
 */
export const API_BASE_URL = '/api/backend';

/** Batch runs are genuinely slow (500 records ≈ 25s), so they get their own budget. */
const DEFAULT_TIMEOUT_MS = 15_000;
const BATCH_TIMEOUT_MS = 120_000;

/**
 * A failure the UI can actually explain to someone.
 *
 * `detail` carries the backend's own message when there is one — FastAPI puts a
 * useful sentence in `detail`, and discarding it in favour of "Request failed"
 * would throw away the only thing that tells the user what to do next.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | null;
  readonly isNetwork: boolean;
  readonly isTimeout: boolean;

  constructor(
    message: string,
    options: {
      status?: number;
      detail?: string | null;
      isNetwork?: boolean;
      isTimeout?: boolean;
    } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = options.status ?? 0;
    this.detail = options.detail ?? null;
    this.isNetwork = options.isNetwork ?? false;
    this.isTimeout = options.isTimeout ?? false;
  }

  /** A sentence safe to render — never a stack trace. */
  get userMessage(): string {
    if (this.isTimeout) {
      return 'The backend took too long to respond. It may still be processing a large batch.';
    }
    if (this.isNetwork) {
      return `Could not reach the Revora API at ${BACKEND_URL}. Check that the backend is running.`;
    }
    if (this.detail) return this.detail;
    if (this.status >= 500) {
      return 'The backend hit an internal error while handling this request.';
    }
    return this.message;
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(init.headers ?? {}),
      },
      cache: 'no-store',
    });
  } catch (error) {
    const aborted = error instanceof Error && error.name === 'AbortError';
    throw new ApiError(
      aborted ? 'Request timed out' : 'Network request failed',
      { isTimeout: aborted, isNetwork: !aborted },
    );
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    // FastAPI returns {"detail": "..."} for HTTPException and a validation
    // array for 422. Both are surfaced rather than flattened into one message.
    let detail: string | null = null;
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') {
        detail = body.detail;
      } else if (Array.isArray(body?.detail)) {
        detail = body.detail
          .map((item: { msg?: string }) => item?.msg)
          .filter(Boolean)
          .join('; ');
      }
    } catch {
      // Non-JSON error body; the status alone will have to do.
    }
    throw new ApiError(`Request failed with status ${response.status}`, {
      status: response.status,
      detail,
    });
  }

  return (await response.json()) as T;
}

/** Build a query string, dropping undefined/empty values rather than sending them. */
function buildQuery(query: object): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue;
    params.set(key, String(value));
  }
  const search = params.toString();
  return search ? `?${search}` : '';
}

export const api = {
  /** GET /health — cheap liveness probe for the connectivity indicator. */
  health(): Promise<HealthResponse> {
    return request<HealthResponse>('/health', { method: 'GET' }, 5_000);
  },

  /**
   * GET /events — the risk-event feed.
   *
   * Filtering is server-side; every key of EventListQuery is a real query
   * parameter on the backend. Undefined values are dropped rather than sent as
   * the string "undefined".
   */
  listEvents(query: EventListQuery = {}): Promise<EventListResponse> {
    return request<EventListResponse>(`/events${buildQuery(query)}`, { method: 'GET' });
  },

  /** GET /events/{id} — one event, end to end. */
  getEvent(eventId: string): Promise<EventDetailResponse> {
    return request<EventDetailResponse>(`/events/${encodeURIComponent(eventId)}`, {
      method: 'GET',
    });
  },

  /** GET /audit — the searchable immutable log. */
  listAudit(query: AuditQuery = {}): Promise<AuditListResponse> {
    return request<AuditListResponse>(`/audit${buildQuery(query)}`, { method: 'GET' });
  },

  /** GET /policies — effective policy for every event type. */
  getPolicies(merchantId?: string): Promise<PolicyListResponse> {
    return request<PolicyListResponse>(
      `/policies${buildQuery({ merchant_id: merchantId })}`,
      { method: 'GET' },
    );
  },

  /** PUT /policies — saves a NEW policy version; the previous one survives. */
  updatePolicy(body: PolicyUpdate): Promise<PolicyOut> {
    return request<PolicyOut>('/policies', {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  },

  /** GET /scripts/{id} — compliance-checked script. Read-only, no side effects. */
  getScript(eventId: string): Promise<ScriptResponse> {
    return request<ScriptResponse>(`/scripts/${encodeURIComponent(eventId)}`, {
      method: 'GET',
    });
  },

  /**
   * GET /scripts/{id}/preview — the same engine, evaluated at a deterministic
   * instant inside the permitted contact window.
   *
   * A demonstration, not a contact. Every other compliance rule still runs for
   * real, and nothing is written. Used so the Hinglish capability is visible
   * outside 08:00-19:00 IST.
   */
  previewScript(eventId: string): Promise<ScriptResponse> {
    return request<ScriptResponse>(
      `/scripts/${encodeURIComponent(eventId)}/preview`,
      { method: 'GET' },
    );
  },

  /** GET /communications — recovery contacts. Read-only. */
  listCommunications(
    channel?: string,
    since?: string,
  ): Promise<CommunicationListResponse> {
    return request<CommunicationListResponse>(
      `/communications${buildQuery({ channel, since })}`,
      { method: 'GET' },
    );
  },

  /**
   * POST /communications/prepare — write the message Revora would send.
   * Contacts nobody; a message refused by policy is recorded with no text.
   */
  prepareCommunication(eventId: string, channel?: string): Promise<CommunicationOut> {
    return request<CommunicationOut>('/communications/prepare', {
      method: 'POST',
      body: JSON.stringify({ event_id: eventId, channel }),
    });
  },

  /** POST /communications/{id}/simulate-send — represents a send. Nothing goes out. */
  simulateSend(id: string): Promise<CommunicationOut> {
    return request<CommunicationOut>(
      `/communications/${encodeURIComponent(id)}/simulate-send`,
      { method: 'POST' },
    );
  },

  /**
   * POST /communications/{id}/simulate-response — represents a customer reply.
   * A commitment to pay creates a real Promise to Pay on the same case.
   */
  simulateResponse(
    id: string,
    body: { response: string; promised_amount?: string; promised_date?: string },
  ): Promise<CommunicationOut> {
    return request<CommunicationOut>(
      `/communications/${encodeURIComponent(id)}/simulate-response`,
      { method: 'POST', body: JSON.stringify(body) },
    );
  },

  /** GET /promises — every promise, with derived merchant status. Read-only. */
  listPromises(status?: string, since?: string): Promise<PromiseListResponse> {
    return request<PromiseListResponse>(`/promises${buildQuery({ status, since })}`, {
      method: 'GET',
    });
  },

  /**
   * Download a report as a PDF.
   *
   * The file is built by the backend from the same rows the dashboard reads,
   * so the document and the screen cannot disagree. The browser only saves it.
   *
   * Returns the blob rather than triggering the save itself, so the caller
   * decides what to do on failure — a half-downloaded report should surface an
   * error, not an empty file.
   */
  async downloadReport(
    kind: 'recovery' | 'audit',
    range: { days?: number; from?: string; to?: string },
  ): Promise<Blob> {
    const query = buildQuery({
      days: range.days,
      date_from: range.from,
      date_to: range.to,
    });
    const response = await fetch(`${API_BASE_URL}/reports/${kind}.pdf${query}`, {
      method: 'GET',
      headers: { Accept: 'application/pdf' },
    });
    if (!response.ok) {
      throw new ApiError('The report could not be generated.', {
        status: response.status,
      });
    }
    return response.blob();
  },

  /** GET /notifications — derived merchant alerts. Read-only. */
  listNotifications(): Promise<NotificationListResponse> {
    return request<NotificationListResponse>('/notifications', { method: 'GET' });
  },

  /** GET /promises/{id} */
  getPromise(id: string): Promise<PromiseOut> {
    return request<PromiseOut>(`/promises/${encodeURIComponent(id)}`, { method: 'GET' });
  },

  /** POST /promises — record a customer's commitment. Contacts nobody. */
  createPromise(body: PromiseCreate): Promise<PromiseOut> {
    return request<PromiseOut>('/promises', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  /**
   * POST /promises/{id}/fulfil — record that the promised payment arrived.
   * Charges nothing; records the consequence of a confirmed payment.
   */
  fulfilPromise(id: string, paidAmount?: string): Promise<PromiseOut> {
    return request<PromiseOut>(`/promises/${encodeURIComponent(id)}/fulfil`, {
      method: 'POST',
      body: JSON.stringify(paidAmount ? { paid_amount: paidAmount } : {}),
    });
  },

  /** POST /promises/{id}/cancel — withdraw a promise. Never becomes a recovery. */
  cancelPromise(id: string): Promise<PromiseOut> {
    return request<PromiseOut>(`/promises/${encodeURIComponent(id)}/cancel`, {
      method: 'POST',
    });
  },

  /** POST /promises/evaluate — record promises whose date has passed unpaid. */
  evaluatePromises(): Promise<PromiseListResponse> {
    return request<PromiseListResponse>('/promises/evaluate', { method: 'POST' });
  },

  /**
   * POST /batch/dry-run — run ONE specified case through the real pipeline.
   *
   * Not a simulation: the case is processed by the same code every batch uses,
   * and the trace is read back from what was actually recorded.
   */
  dryRun(body: DryRunRequest): Promise<DryRunResponse> {
    return request<DryRunResponse>('/batch/dry-run', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  /** GET /batch/runs — completed runs, newest first. Read-only. */
  listRuns(limit = 8): Promise<RunListResponse> {
    return request<RunListResponse>(`/batch/runs?limit=${limit}`, { method: 'GET' });
  },

  /**
   * GET /batch/runs/{id} — reopen a completed run.
   *
   * Returns the stored snapshot, so a past run always shows the figures the
   * merchant actually saw rather than a fresh recomputation.
   */
  getRun(runId: string): Promise<RunDetailResponse> {
    return request<RunDetailResponse>(`/batch/runs/${encodeURIComponent(runId)}`, {
      method: 'GET',
    });
  },

  /**
   * POST /batch — run N synthetic records through the real pipeline.
   *
   * This MUTATES: it detects, diagnoses, decides, gates and executes, then
   * writes the ledger and audit trail. It is only ever called from an explicit
   * user action, never on page load.
   */
  runBatch(body: BatchRequest): Promise<BatchResponse> {
    return request<BatchResponse>(
      '/batch',
      { method: 'POST', body: JSON.stringify(body) },
      BATCH_TIMEOUT_MS,
    );
  },
};

/* --------------------------------------------------------------------------
 * Formatting
 * --------------------------------------------------------------------------
 * Kept beside the client because it is the other half of "what the API sends"
 * — the backend sends exact decimal strings and these turn them into something
 * a person reads, without ever doing arithmetic on a float.
 */

/** Parse an exact decimal string. Returns 0 for anything unparseable. */
export function toNumber(value: string | number | null | undefined): number {
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0;
  if (!value) return 0;
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

const inrFull = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
});

const inrExact = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** ₹63,40,340 — Indian digit grouping, no decimals. For display. */
export function formatInr(value: string | number): string {
  return inrFull.format(toNumber(value));
}

/** ₹63,40,340.34 — full precision, for tooltips and title attributes. */
export function formatInrExact(value: string | number): string {
  return inrExact.format(toNumber(value));
}

/** ₹63.4L / ₹1.2Cr — compact, for chart axes where space is tight. */
export function formatInrCompact(value: string | number): string {
  const amount = toNumber(value);
  const abs = Math.abs(amount);
  if (abs >= 1_00_00_000) return `₹${(amount / 1_00_00_000).toFixed(1)}Cr`;
  if (abs >= 1_00_000) return `₹${(amount / 1_00_000).toFixed(1)}L`;
  if (abs >= 1_000) return `₹${(amount / 1_000).toFixed(1)}k`;
  return `₹${Math.round(amount)}`;
}

/** 12.9% — the backend already sends a 0-1 rate. */
export function formatPercent(rate: number, digits = 1): string {
  return `${(rate * 100).toFixed(digits)}%`;
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat('en-IN').format(value);
}

/** "3 days ago" — relative time, with the exact stamp always available alongside. */
export function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 45) return 'just now';
  const units: Array<[number, Intl.RelativeTimeFormatUnit]> = [
    [60, 'second'],
    [3600, 'minute'],
    [86400, 'hour'],
    [2592000, 'day'],
    [31536000, 'month'],
  ];
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  let value = seconds;
  let unit: Intl.RelativeTimeFormatUnit = 'second';
  for (let i = 0; i < units.length; i += 1) {
    const [limit, name] = units[i];
    if (Math.abs(seconds) < limit) {
      unit = name;
      const divisor = i === 0 ? 1 : units[i - 1][0];
      value = Math.round(seconds / divisor);
      break;
    }
    if (i === units.length - 1) {
      unit = 'year';
      value = Math.round(seconds / limit);
    }
  }
  return formatter.format(-value, unit);
}

/** "23 Aug 2026, 14:32" — the exact stamp, for titles and detail pages. */
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(undefined, {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** "14:32:07" — runs are seconds apart, so the time of day is what identifies one. */
export function formatClock(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/** Human label for an event type key that may be absent from the enum. */
export function humanizeKey(key: string): string {
  return key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

export type { EventType };

/* --------------------------------------------------------------------------
 * Reporting periods
 * --------------------------------------------------------------------------
 * A period is a real date window sent to the API as detected_from/detected_to,
 * which recomputes every amount server-side from the ledger. Nothing here
 * filters in the browser, so the selector genuinely changes the numbers rather
 * than relabelling the same ones.
 */

export type PeriodKey = 'this_month' | 'last_6_months' | 'last_12_months' | 'all_time';

export const PERIODS: Array<{ key: PeriodKey; label: string }> = [
  { key: 'this_month', label: 'This month' },
  { key: 'last_6_months', label: 'Last 6 months' },
  { key: 'last_12_months', label: 'Last 12 months' },
  { key: 'all_time', label: 'All time' },
];

export interface PeriodWindow {
  from?: string;
  to?: string;
}

/** Start of the window for a period. All-time deliberately has no lower bound. */
export function periodWindow(key: PeriodKey, now = new Date()): PeriodWindow {
  if (key === 'all_time') return {};
  if (key === 'this_month') {
    return { from: new Date(now.getFullYear(), now.getMonth(), 1).toISOString() };
  }
  const months = key === 'last_6_months' ? 6 : 12;
  const from = new Date(now.getFullYear(), now.getMonth() - (months - 1), 1);
  return { from: from.toISOString() };
}

export interface TrendBucket {
  label: string;
  from: string;
  to: string;
}

/**
 * Time buckets for the trend chart.
 *
 * Weekly within a month, monthly across longer spans — a twelve-month chart
 * drawn daily is unreadable, and a one-month chart drawn monthly is a single
 * bar. All-time starts at the earliest event actually recorded, so the axis
 * never extends into months that never existed.
 */
export function trendBuckets(
  key: PeriodKey,
  earliest: string | null,
  now = new Date(),
): TrendBucket[] {
  const buckets: TrendBucket[] = [];

  if (key === 'this_month') {
    const start = new Date(now.getFullYear(), now.getMonth(), 1);
    let cursor = start;
    let week = 1;
    while (cursor <= now) {
      const end = new Date(cursor);
      end.setDate(end.getDate() + 6);
      end.setHours(23, 59, 59, 999);
      buckets.push({
        label: `Week ${week}`,
        from: cursor.toISOString(),
        to: (end > now ? now : end).toISOString(),
      });
      const next = new Date(cursor);
      next.setDate(next.getDate() + 7);
      cursor = next;
      week += 1;
    }
    return buckets;
  }

  let months = key === 'last_6_months' ? 6 : 12;
  if (key === 'all_time') {
    const first = earliest ? new Date(earliest) : now;
    months =
      (now.getFullYear() - first.getFullYear()) * 12 +
      (now.getMonth() - first.getMonth()) +
      1;
    months = Math.max(1, Math.min(months, 24));
  }

  for (let index = months - 1; index >= 0; index -= 1) {
    const start = new Date(now.getFullYear(), now.getMonth() - index, 1);
    const end = new Date(now.getFullYear(), now.getMonth() - index + 1, 0, 23, 59, 59, 999);
    buckets.push({
      label: start.toLocaleDateString(undefined, { month: 'short' }),
      from: start.toISOString(),
      to: end.toISOString(),
    });
  }
  return buckets;
}

/**
 * How much history actually exists, in whole months.
 *
 * Used to tell the merchant "only N months of recovery history are available"
 * rather than drawing an empty year and letting them assume recovery collapsed.
 */
export function monthsOfHistory(earliest: string | null, now = new Date()): number {
  if (!earliest) return 0;
  const first = new Date(earliest);
  if (Number.isNaN(first.getTime())) return 0;
  return (
    (now.getFullYear() - first.getFullYear()) * 12 + (now.getMonth() - first.getMonth()) + 1
  );
}

/* --------------------------------------------------------------------------
 * Live synchronisation
 * --------------------------------------------------------------------------
 * Revora works on its own; the interface just has to keep up. This is the ONE
 * place that decides how often the UI re-reads the backend, so pages cannot
 * drift into having their own refresh behaviour and showing each other
 * different numbers.
 *
 * This interval is the UI's, not the engine's. Recovery happens when Revora
 * processes an event, on its own cadence — polling more often would only make
 * the screen redraw sooner, never make recovery happen faster.
 */

export const LIVE_REFRESH_MS = 5_000;

export type LiveStatus = 'live' | 'reconnecting';
