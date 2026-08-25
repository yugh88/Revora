/**
 * TypeScript mirrors of the backend's Pydantic schemas.
 *
 * These are transcribed from backend/app/schemas/batch.py and backend/app/enums.py
 * as they actually exist — not guessed. Anything the backend does not currently
 * return has no type here, so the compiler stops a component from rendering a
 * field the API will never send.
 *
 * Money crosses the wire as a STRING. That is deliberate on the backend side:
 * the ledger stores exact paise and a JSON number would arrive in JavaScript as
 * a float and start drifting the moment anything summed it. Parse only at the
 * point of display, never to do arithmetic that is then displayed as money.
 */

/** backend/app/enums.py — EventType. Exactly five, no others may be added. */
export type EventType =
  | 'payment_degraded'
  | 'checkout_abandoned'
  | 'subscription_failed'
  | 'invoice_overdue'
  | 'mandate_failed';

export const EVENT_TYPES: EventType[] = [
  'payment_degraded',
  'checkout_abandoned',
  'subscription_failed',
  'invoice_overdue',
  'mandate_failed',
];

/** backend/app/enums.py — EventStatus. */
export type EventStatus =
  | 'open'
  | 'diagnosing'
  | 'intervening'
  | 'recovered'
  | 'escalated'
  | 'unrecoverable'
  | 'stopped';

/** backend/app/enums.py — GatewayUsed. */
export type GatewayUsed = 'local_simulation' | 'razorpay_test';

/** BatchRequest. Section 10: default 50, supports 500. */
export interface BatchRequest {
  count: number;
  gateway?: GatewayUsed;
  seed?: number;
}

/** BatchMoney. Every field an exact decimal string, not a number. */
export interface BatchMoney {
  amount_at_risk: string;
  amount_attempted: string;
  amount_recovered: string;
  amount_lost: string;
  amount_pending: string;
  currency: string;
}

/** StoppingRuleTriggers — the four reasons Section 10 names, plus the rest. */
export interface StoppingRuleTriggers {
  cooldown: number;
  do_not_contact: number;
  max_attempts: number;
  hard_decline: number;
  other: Record<string, number>;
}

/** IsolatedFailure — one record that failed without taking the batch down. */
export interface IsolatedFailure {
  record_index: number;
  event_id: string | null;
  correlation_id: string | null;
  stage: string;
  error_type: string;
  error_message: string;
}

/** BatchResponse — the full Section 10 metric set. */
export interface BatchResponse {
  batch_id: string;
  correlation_id: string;
  gateway: GatewayUsed;
  seed: number;
  started_at: string;
  finished_at: string;
  duration_seconds: number;

  total_records: number;
  processed: number;
  isolated_failures: number;
  skipped_duplicates: number;

  money: BatchMoney;
  recovery_rate: number;
  resolution_rate: number;

  escalation_ceiling_hits: number;
  stopping_rule_triggers: StoppingRuleTriggers;
  promises_made: number;
  promises_kept: number;
  promises_broken: number;

  status_breakdown: Partial<Record<EventStatus, number>>;
  action_breakdown: Record<string, number>;
  event_type_breakdown: Partial<Record<EventType, number>>;
  outcome_breakdown: Record<string, number>;

  /** null when no ML opinion was available — an absent opinion is NOT a
   *  disagreement, so this must never be coerced to 0. */
  ml_agreement_rate: number | null;
  ml_predictions: number;
  ml_agreements: number;
  ml_unavailable: number;

  exceptions_raised: number;
  audit_entries: number;

  failures: IsolatedFailure[];
}

/** GET /health. */
export interface HealthResponse {
  status: string;
  version: string;
}

/**
 * One completed analysis, as the dashboard holds it.
 *
 * The backend has no run-history endpoint, so history is accumulated in the
 * browser for the current session only and is labelled as such in the UI. It is
 * real measured data — each entry is one actual BatchResponse — but it is not
 * persisted, and the dashboard never implies otherwise.
 */
export interface AnalysisRun {
  index: number;
  ranAt: string;
  response: BatchResponse;
}

/** Human labels for the five event types. */
export const EVENT_TYPE_LABELS: Record<EventType, string> = {
  payment_degraded: 'Payment degraded',
  checkout_abandoned: 'Checkout abandoned',
  subscription_failed: 'Subscription failed',
  invoice_overdue: 'Invoice overdue',
  mandate_failed: 'Mandate failed',
};

/** One-line description of what each direction actually is. */
export const EVENT_TYPE_HINTS: Record<EventType, string> = {
  payment_degraded: 'A charge failed at the gateway',
  checkout_abandoned: 'Customer left before paying',
  subscription_failed: 'A recurring charge did not go through',
  invoice_overdue: 'An issued invoice is past its due date',
  mandate_failed: 'A NACH or UPI autopay mandate did not execute',
};
