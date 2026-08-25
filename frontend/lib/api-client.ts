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
  BatchRequest,
  BatchResponse,
  EventType,
  HealthResponse,
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

export const api = {
  /** GET /health — cheap liveness probe for the connectivity indicator. */
  health(): Promise<HealthResponse> {
    return request<HealthResponse>('/health', { method: 'GET' }, 5_000);
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
