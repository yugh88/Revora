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

/* --------------------------------------------------------------------------
 * Events — GET /events and GET /events/{id}
 * --------------------------------------------------------------------------
 * Transcribed from backend/app/schemas/risk_event.py. Read-only: the backend
 * exposes no mutation on an individual event, so there is no request type here
 * and the UI must not offer an action it cannot perform.
 */

/** One row of the feed. */
export interface EventSummary {
  id: string;
  type: EventType;
  merchant_id: string;
  customer_id: string;
  /** The customer's name. The UI shows people, never identifiers. */
  customer_name: string;
  amount: string;
  currency: string;
  source_ref: string | null;
  detected_at: string;
  status: EventStatus;
  gateway_used: GatewayUsed;
  correlation_id: string;

  root_cause: string | null;
  confidence: number | null;

  action_code: string | null;
  recovery_probability: number | null;
  policy_status: string | null;

  /** null when the classifier had no opinion — NOT a disagreement. */
  ml_agrees: boolean | null;

  needs_review: boolean;
  review_reasons: string[];

  resolved: string | null;
  amount_recovered: string | null;
}

export interface EventMoneySummary {
  amount_at_risk: string;
  amount_recovered: string;
  amount_lost: string;
  amount_pending: string;
  currency: string;
  recovery_rate: number;
  active_interventions: number;
}

export interface EventListResponse {
  total: number;
  returned: number;
  limit: number;
  offset: number;
  status_breakdown: Record<string, number>;
  type_breakdown: Record<string, number>;
  needs_review_count: number;
  money: EventMoneySummary;
  /** Oldest event in the WHOLE ledger, ignoring filters. */
  earliest_detected_at: string | null;
  items: EventSummary[];
}

/** Server-side filters. Every key maps to a real query parameter. */
export interface EventListQuery {
  detected_from?: string;
  detected_to?: string;
  status?: EventStatus;
  type?: EventType;
  gateway?: GatewayUsed;
  needs_review?: boolean;
  q?: string;
  limit?: number;
  offset?: number;
  order?: 'asc' | 'desc';
}

export interface DiagnosisDetail {
  root_cause: string;
  confidence: number;
  evidence: string[];
  diagnosed_at: string;
  is_low_confidence: boolean;
}

/** The classifier's independent opinion. Never the authority on the action. */
export interface MlDetail {
  predicted_root_cause: string;
  confidence: number;
  agrees_with_rule_engine: boolean;
  model_version: string;
  predicted_at: string;
}

export interface DecisionDetail {
  id: number;
  action_code: string;
  recovery_probability: number;
  probability_source: string;
  policy_result: PolicyResult;
  policy_version: number;
  decision_factors: Record<string, unknown>;
  reasoning_text: string;
  decided_at: string;
}

/** Section 4 fixes these five keys exactly. */
export interface PolicyResult {
  status: 'allowed' | 'blocked';
  rule_triggered: string | null;
  threshold_checked: string | null;
  actual_value: unknown;
  threshold_value: unknown;
}

export interface StoppingRuleDetail {
  attempts_used: number;
  max_attempts_for_type: number;
  cooldown_until: string | null;
  do_not_contact_snapshot: boolean;
  escalation_level: number;
  hard_stop_reason: string | null;
}

export interface AttemptDetail {
  id: string;
  attempt_number: number;
  status: string;
  failure_reason: string | null;
  provider_ref: string | null;
  gateway_used: string;
  initiated_at: string;
  resolved_at: string | null;
}

export interface OutcomeDetail {
  resolved: string;
  amount_recovered: string;
  resolved_at: string | null;
  resolution_channel: string | null;
}

export interface AuditEntry {
  id: number;
  timestamp: string;
  /** Present on GET /audit; omitted on the event drill-down, where every entry
   *  already belongs to the event being viewed. */
  event_id?: string | null;
  correlation_id?: string;
  stage: string;
  action: string;
  actor: string;
  before_state: unknown;
  after_state: unknown;
  reasoning: string | null;
}

export interface EventDetailResponse {
  event: EventSummary;
  diagnosis: DiagnosisDetail | null;
  ml: MlDetail | null;
  decisions: DecisionDetail[];
  stopping_rule_state: StoppingRuleDetail | null;
  attempts: AttemptDetail[];
  outcome: OutcomeDetail | null;
  audit: AuditEntry[];
  stages_present: string[];
  stages_missing: string[];
}

/** The Section 2 pipeline, in order. */
export const PIPELINE_STAGES = [
  'detection',
  'diagnosis',
  'decision',
  'policy',
  'execution',
  'verification',
  'recovery',
  'escalation',
] as const;

export type PipelineStage = (typeof PIPELINE_STAGES)[number];

/* --------------------------------------------------------------------------
 * Policies — GET /policies, PUT /policies
 * -------------------------------------------------------------------------- */

export interface PolicyOut {
  policy_version: number;
  merchant_id: string;
  event_type: EventType;
  max_attempts: number;
  cooldown_hours: number;
  amount_threshold: string;
  recovery_probability_threshold: number;
  contact_limit_per_channel: number;
  escalation_ceiling: number;
  updated_at: string | null;
  /** True when no merchant row exists and these are the engine's own defaults. */
  is_default: boolean;
}

export interface PolicyListResponse {
  merchant_id: string;
  total: number;
  items: PolicyOut[];
}

export interface PolicyUpdate {
  merchant_id: string;
  event_type: EventType;
  max_attempts: number;
  cooldown_hours: number;
  amount_threshold: string;
  recovery_probability_threshold: number;
  contact_limit_per_channel: number;
  escalation_ceiling: number;
}

/* --------------------------------------------------------------------------
 * Scripts — GET /scripts/{event_id}
 * -------------------------------------------------------------------------- */

export interface ComplianceCheck {
  rule_id: string;
  description: string;
  passed: boolean;
  detail: string;
}

export interface ScriptResponse {
  event_id: string;
  event_type: string;
  customer_id: string;
  amount: string;
  currency: string;
  /** Empty when compliance refused. Never partially rendered. */
  script: string;
  reasoning: string;
  tone: 'friendly' | 'neutral' | 'formal';
  urgency: 'low' | 'medium' | 'high';
  channel: string;
  language: string;
  compliant: boolean;
  compliance_checks: ComplianceCheck[];
  failure_reason: string | null;
  template_key: string;
  slots_used: Record<string, unknown>;
  /** True only on the /preview path. A preview is a rendering demonstration,
   *  never a contact — the live endpoint always returns false. */
  is_preview: boolean;
  /** The instant the contact-window rule was evaluated against. Null on the
   *  live path, where the real current time was used. */
  preview_time: string | null;
}

/* --------------------------------------------------------------------------
 * Audit — GET /audit
 * -------------------------------------------------------------------------- */

export interface AuditListResponse {
  total: number;
  returned: number;
  offset: number;
  limit: number;
  stage_breakdown: Record<string, number>;
  items: AuditEntry[];
}

export interface AuditQuery {
  event_id?: string;
  correlation_id?: string;
  stage?: string;
  actor?: string;
  action?: string;
  order?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
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

/* --------------------------------------------------------------------------
 * Recovery run history — GET /batch/runs, GET /batch/runs/{id}
 * -------------------------------------------------------------------------- */

export interface RunSummary {
  id: string;
  name: string;
  finished_at: string;
  gateway: GatewayUsed;
  total_records: number;
  processed: number;
  amount_at_risk: string;
  amount_recovered: string;
  amount_pending: string;
  amount_lost: string;
  recovery_rate: number;
  recovered_count: number;
  escalated_count: number;
}

export interface RunListResponse {
  total: number;
  items: RunSummary[];
}

export interface RunDetailResponse {
  run: RunSummary;
  /** The full response that run returned when it finished. */
  snapshot: BatchResponse;
}

/* --------------------------------------------------------------------------
 * Promise to Pay
 * -------------------------------------------------------------------------- */

/** The state a merchant sees. Derived by the backend from the promised date. */
export type PromiseStatus = 'promised' | 'due_soon' | 'fulfilled' | 'overdue' | 'cancelled';

export interface PromiseOut {
  id: string;
  customer_name: string;
  promised_amount: string;
  currency: string;
  promised_date: string;
  created_at: string;
  resolved_at: string | null;
  status: PromiseStatus;
  event_id: string;
  event_type: EventType;
  amount_at_risk: string;
  /** What the customer actually said, verbatim. */
  source_response: string | null;
  /** What Revora will do next, in plain language. */
  next_step: string;
  /** Read back from the ledger, never inferred from the promise. */
  recovered: boolean;
  amount_recovered: string;
}

export interface PromiseListResponse {
  total: number;
  status_breakdown: Record<string, number>;
  total_promised: string;
  total_fulfilled: string;
  items: PromiseOut[];
}

export interface PromiseCreate {
  event_id: string;
  promised_amount: string;
  promised_date: string;
}

/* --------------------------------------------------------------------------
 * Communications — Email / SMS / Voice
 * --------------------------------------------------------------------------
 * There is no "sent" or "delivered" status, deliberately: Revora has no
 * provider, so the type system offers nothing the UI could use to claim a
 * customer was contacted.
 */

export type CommunicationChannel = 'email' | 'sms' | 'voice_script' | 'in_app';
export type CommunicationStatus = 'prepared' | 'simulated' | 'blocked';
export type CustomerResponse = 'promised_to_pay' | 'paid' | 'no_response';

export interface CommunicationOut {
  id: string;
  customer_name: string;
  channel: CommunicationChannel;
  status: CommunicationStatus;
  /** Empty when compliance refused. Never partially rendered. */
  body: string;
  reason: string;
  /** Why the agent chose this channel, in merchant language. */
  channel_reason: string;
  blocked_reason: string | null;
  /** Always true. No provider integration exists. */
  is_simulated: boolean;
  created_at: string;
  simulated_at: string | null;
  customer_response: CustomerResponse | null;
  responded_at: string | null;
  promise_id: string | null;
  event_id: string;
  event_type: EventType;
  amount_at_risk: string;
}

export interface CommunicationListResponse {
  total: number;
  channel_breakdown: Record<string, number>;
  status_breakdown: Record<string, number>;
  items: CommunicationOut[];
}

/* --------------------------------------------------------------------------
 * Notifications — derived from real state, never stored
 * -------------------------------------------------------------------------- */

export interface MerchantNotification {
  id: string;
  kind: string;
  title: string;
  detail: string;
  severity: 'info' | 'attention' | 'good';
  occurred_at: string;
  href: string;
}

export interface NotificationListResponse {
  total: number;
  items: MerchantNotification[];
}
