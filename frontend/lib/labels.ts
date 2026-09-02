/**
 * One place that turns backend identifiers into words a merchant understands.
 *
 * Before this existed, six pages each called a generic `humanizeKey` helper that
 * did nothing but swap underscores for spaces. That produced "Hard decline",
 * "Contact time window" and "By root cause.unknown.neutral" in primary UI —
 * technically derived from the right value, and still meaningless to anyone who
 * has not read the source. Worse, six copies of a transformation drift: one page
 * would title-case, another would not.
 *
 * Every map here is keyed on a value the backend actually emits, transcribed
 * from app/enums.py, app/engine/diagnosis_engine.py and
 * app/templates/compliance_rules.yaml. An unmapped value falls back to a readable
 * sentence rather than throwing, because a new backend enum should degrade to
 * "Some new thing", never to a blank cell or a crash.
 *
 * These labels are what the UI shows, everywhere. There is no escape hatch that
 * surfaces the raw identifier instead: a merchant screen showing `not_authenticated`
 * has failed regardless of which section it appears in. The exact codes remain in
 * the database, the API and the source, where the people who need them look.
 */

/* -------------------------------------------------------------------------- */
/* Fallback                                                                    */
/* -------------------------------------------------------------------------- */

/** "some_raw_value" -> "Some raw value". Last resort, never the first choice. */
export function toSentence(value: string): string {
  if (!value) return '—';
  const spaced = value.replace(/[_.]+/g, ' ').trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

function lookup(map: Record<string, string>, value: string | null | undefined): string {
  if (!value) return '—';
  return map[value] ?? toSentence(value);
}

/* -------------------------------------------------------------------------- */
/* The five recovery directions                                                */
/* -------------------------------------------------------------------------- */

export const EVENT_TYPE_LABEL: Record<string, string> = {
  payment_degraded: 'Payment degraded',
  checkout_abandoned: 'Checkout abandoned',
  subscription_failed: 'Subscription failed',
  invoice_overdue: 'Invoice overdue',
  mandate_failed: 'Mandate failed',
};

/** What each direction means, in one line, for tooltips and empty states. */
export const EVENT_TYPE_HINT: Record<string, string> = {
  payment_degraded: 'A charge failed at the gateway',
  checkout_abandoned: 'Customer left before paying',
  subscription_failed: 'A recurring charge did not go through',
  invoice_overdue: 'An issued invoice is past its due date',
  mandate_failed: 'A NACH or UPI autopay mandate did not execute',
};

export const eventTypeLabel = (v?: string | null) => lookup(EVENT_TYPE_LABEL, v);

/* -------------------------------------------------------------------------- */
/* Root causes — why the money stopped                                         */
/* -------------------------------------------------------------------------- */

export const ROOT_CAUSE_LABEL: Record<string, string> = {
  // payment_degraded
  card_expired: 'Card expired',
  insufficient_funds: 'Insufficient funds',
  issuer_declined: 'Bank declined the payment',
  network_timeout: 'Network timeout',
  '3ds_failed': '3D Secure check failed',
  bank_server_down: 'Bank server unavailable',
  risk_engine_blocked: 'Blocked by risk checks',
  // checkout_abandoned
  payment_step_dropped: 'Left at the payment step',
  otp_timeout: 'OTP timed out',
  price_shock: 'Price changed before paying',
  no_preferred_method: 'Preferred payment method unavailable',
  session_expired: 'Checkout session expired',
  unknown: 'Unknown payment issue',
  // subscription_failed
  mandate_revoked: 'Mandate revoked',
  user_paused: 'Customer paused the subscription',
  halted_after_max_retries: 'Subscription halted by the gateway',
  // invoice_overdue
  forgotten: 'Invoice overlooked',
  disputed_amount: 'Amount disputed',
  awaiting_approval: 'Awaiting internal approval',
  cash_flow_delay: 'Cash-flow delay',
  delivery_failure: 'Delivery problem',
  broken_ptp: 'Promise to pay not kept',
  // mandate_failed
  not_authenticated: 'Mandate not authenticated',
  insufficient_balance: 'Insufficient balance',
  bank_rejected: 'Bank rejected the mandate',
  expired: 'Mandate expired',
  revoked: 'Mandate revoked',
};

export const rootCauseLabel = (v?: string | null) => lookup(ROOT_CAUSE_LABEL, v);

/* -------------------------------------------------------------------------- */
/* Actions — what Revora decided to do                                         */
/* -------------------------------------------------------------------------- */

export const ACTION_LABEL: Record<string, string> = {
  update_card_email: 'Email a card-update link',
  sms_reminder: 'Send an SMS reminder',
  in_app_nudge: 'Show an in-app nudge',
  email_saved_cart: 'Email the saved cart',
  await_gateway_auto_retry: 'Wait for the gateway retry',
  friendly_reminder: 'Send a friendly reminder',
  reminder_with_call_script: 'Reminder with a call script',
  formal_notice: 'Send a formal notice',
  reauth_nudge: 'Ask the customer to re-authorise',
  retry_payment: 'Retry the payment',
  retry_salary_window: 'Retry near the salary date',
  final_retry: 'Final retry attempt',
  human_handoff: 'Hand over to a person',
  no_action: 'No action taken',
};

export const actionLabel = (v?: string | null) => lookup(ACTION_LABEL, v);

/* -------------------------------------------------------------------------- */
/* Compliance rules — item 10                                                  */
/* -------------------------------------------------------------------------- */

export const COMPLIANCE_RULE_LABEL: Record<string, string> = {
  contact_time_window: 'Contact-time window',
  frequency_cap: 'Contact frequency limit',
  no_false_urgency: 'No false urgency',
  no_coercive_language: 'Communication safety',
};

export const complianceRuleLabel = (v?: string | null) =>
  lookup(COMPLIANCE_RULE_LABEL, v);

/* -------------------------------------------------------------------------- */
/* Why Revora stopped                                                          */
/* -------------------------------------------------------------------------- */

export const STOP_REASON_LABEL: Record<string, string> = {
  hard_decline: 'Permanent decline',
  hard_stop_cause: 'Permanent decline',
  do_not_contact: 'Customer opted out',
  cooldown: 'Cooldown period active',
  cooldown_active: 'Cooldown period active',
  max_attempts: 'Attempt limit reached',
  max_attempts_reached: 'Attempt limit reached',
  escalation_ceiling: 'Escalation limit reached',
  amount_threshold_requires_human: 'Large amount — needs a person',
  recovery_probability_below_threshold: 'Not worth pursuing',
  contact_limit_per_channel: 'Contact frequency limit',
  no_action_permitted_by_intervention_table: 'No permitted action remained',
};

export const stopReasonLabel = (v?: string | null) => lookup(STOP_REASON_LABEL, v);

/* -------------------------------------------------------------------------- */
/* Pipeline stages and audit actions                                           */
/* -------------------------------------------------------------------------- */

export const STAGE_LABEL: Record<string, string> = {
  detection: 'Detected',
  diagnosis: 'Diagnosed',
  decision: 'Decided',
  policy: 'Policy check',
  execution: 'Action taken',
  verification: 'Verified',
  recovery: 'Recovered',
  escalation: 'Escalated',
};

export const stageLabel = (v?: string | null) => lookup(STAGE_LABEL, v);

export const AUDIT_ACTION_LABEL: Record<string, string> = {
  event_detected: 'Revenue at risk detected',
  state_transition: 'Status changed',
  decision_made: 'Action chosen',
  policy_evaluated: 'Policy checked',
  gateway_execution: 'Executed via gateway',
  contact_sent: 'Message sent to customer',
  outcome_verified: 'Outcome verified',
  human_handoff: 'Handed to a person',
  recovered_externally: 'Customer paid independently',
  execution_skipped_idempotent: 'Skipped — already executed',
  invalid_transition_rejected: 'Invalid change rejected',
  ml_rule_disagreement: 'Second opinion disagreed',
  ml_unavailable: 'Second opinion unavailable',
  lock_reclaimed: 'Stalled job reclaimed',
  // Promise-to-Pay, in the language a merchant would use to describe it.
  promise_to_pay_recorded: 'Customer promised to pay',
  promise_fulfilled: 'Promise fulfilled — payment received',
  promise_broken: 'Promise overdue — payment not received',
  promise_cancelled: 'Promise withdrawn',
};

export const auditActionLabel = (v?: string | null) => lookup(AUDIT_ACTION_LABEL, v);

/* -------------------------------------------------------------------------- */
/* Outcomes, channels, gateways, tone and urgency                              */
/* -------------------------------------------------------------------------- */

export const OUTCOME_LABEL: Record<string, string> = {
  recovered: 'Recovered',
  partially_recovered: 'Partly recovered',
  lost: 'Written off',
  pending: 'Still in progress',
};

export const outcomeLabel = (v?: string | null) => lookup(OUTCOME_LABEL, v);

export const CHANNEL_LABEL: Record<string, string> = {
  email: 'Email',
  sms: 'SMS',
  in_app: 'In-app',
  whatsapp: 'WhatsApp',
  voice_script: 'Voice',
  human_handoff: 'Person',
  b2b: 'B2B',
  external: 'Paid independently',
  none: 'No contact',
};

export const channelLabel = (v?: string | null) => lookup(CHANNEL_LABEL, v);

export const GATEWAY_LABEL: Record<string, string> = {
  local_simulation: 'Built-in Simulator',
  razorpay_test: 'Razorpay Test Sandbox',
};

export const gatewayLabel = (v?: string | null) => lookup(GATEWAY_LABEL, v);

export const TONE_LABEL: Record<string, string> = {
  friendly: 'Friendly',
  neutral: 'Neutral',
  formal: 'Formal',
};

export const toneLabel = (v?: string | null) => lookup(TONE_LABEL, v);

export const URGENCY_LABEL: Record<string, string> = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
};

export const urgencyLabel = (v?: string | null) => lookup(URGENCY_LABEL, v);

/* -------------------------------------------------------------------------- */
/* Template keys                                                               */
/* -------------------------------------------------------------------------- */

/**
 * "by_root_cause.card_expired.friendly" -> "Card expired · Friendly".
 *
 * A merchant needs to know the message is tuned to the cause and struck in the
 * right tone. The key that encodes that is not something they should have to
 * parse.
 */
export function templateKeyLabel(key?: string | null): string {
  if (!key) return '—';
  const parts = key.split('.');
  if (parts[0] === 'by_root_cause' && parts.length >= 3) {
    return `${rootCauseLabel(parts[1])} · ${toneLabel(parts[2])}`;
  }
  if (parts[0] === 'by_event_type' && parts.length >= 2) {
    return eventTypeLabel(parts[1]);
  }
  if (parts[0] === 'default' && parts.length >= 2) {
    return `Standard message · ${toneLabel(parts[1])}`;
  }
  return toSentence(key);
}

/* -------------------------------------------------------------------------- */
/* Policy fields                                                               */
/* -------------------------------------------------------------------------- */

export const POLICY_STATUS_LABEL: Record<string, string> = {
  allowed: 'Action allowed',
  blocked: 'Action blocked',
};

export const policyStatusLabel = (v?: string | null) => lookup(POLICY_STATUS_LABEL, v);

/* --------------------------------------------------------------------------
 * Merchant-readable case identity and reasoning
 * --------------------------------------------------------------------------
 * The engine records its own reasoning for the audit trail, and that text is
 * deliberately precise: it names the internal cause code, the confidence, the
 * rule that fired and the identifier of the row it acted on. That is exactly
 * what an auditor needs and exactly what a merchant should never be handed.
 *
 * These helpers compose the same facts into plain language from STRUCTURED
 * fields. Nothing is invented — every sentence is assembled from the cause,
 * the action and the policy verdict the engine actually recorded. The precise
 * version stays in the database and the API for the people who need it.
 */

const CASE_KIND: Record<string, string> = {
  payment_degraded: 'Payment recovery',
  checkout_abandoned: 'Checkout recovery',
  subscription_failed: 'Subscription recovery',
  invoice_overdue: 'Invoice recovery',
  mandate_failed: 'Mandate recovery',
};

/** "Invoice recovery — Pooja Iyer". Never an identifier. */
export function caseTitle(eventType?: string | null, customerName?: string | null): string {
  const kind = eventType ? (CASE_KIND[eventType] ?? 'Recovery') : 'Recovery';
  return customerName ? `${kind} — ${customerName}` : kind;
}

export const caseKind = (eventType?: string | null) =>
  eventType ? (CASE_KIND[eventType] ?? 'Recovery') : 'Recovery';

/**
 * Why this happened, in a sentence.
 *
 * When the engine could not reach a confident conclusion, that is stated
 * plainly rather than dressed up with a percentage — the merchant's takeaway is
 * "a person should look at this", not "0.4".
 */
export function causeExplanation(
  eventType?: string | null,
  rootCause?: string | null,
  needsReview = false,
): string {
  const subject = {
    payment_degraded: 'the payment failed',
    checkout_abandoned: 'the checkout was not completed',
    subscription_failed: 'the subscription charge did not go through',
    invoice_overdue: 'the invoice is unpaid',
    mandate_failed: 'the autopay mandate did not run',
  }[eventType ?? ''] ?? 'this happened';

  if (needsReview || !rootCause || rootCause === 'unknown') {
    return `Revora could not confidently determine why ${subject}, so it did not take an automatic financial action. This case was sent for human review.`;
  }
  return `Revora determined that ${subject} because of ${rootCauseLabel(rootCause).toLowerCase()}.`;
}

/**
 * What Revora decided and why, in a sentence.
 *
 * The policy verdict carries the reason it fired, translated — so "blocked on
 * do_not_contact" becomes "the customer has opted out of recovery contact".
 */
export function decisionExplanation(
  action?: string | null,
  policyStatus?: string | null,
  ruleTriggered?: string | null,
): string {
  if (policyStatus === 'blocked') {
    const because = ruleTriggered ? POLICY_REASON[ruleTriggered] : null;
    return because
      ? `Revora did not contact this customer because ${because}.`
      : 'Revora did not act on this case because your policy did not allow it.';
  }
  if (!action || action === 'no_action') {
    return 'No recovery action was appropriate for this case.';
  }
  return `Revora chose to ${actionLabel(action).toLowerCase()}, which was within every limit you have set.`;
}

/** Why the policy refused, in merchant terms. */
const POLICY_REASON: Record<string, string> = {
  do_not_contact: 'the customer has opted out of recovery contact',
  hard_stop_cause: 'the bank declined permanently and retrying would not help',
  cooldown_active: 'this customer was contacted too recently',
  cooldown: 'this customer was contacted too recently',
  max_attempts_reached: 'the maximum number of attempts has already been made',
  max_attempts: 'the maximum number of attempts has already been made',
  contact_limit_per_channel: 'the contact limit for this customer has been reached',
  amount_threshold_requires_human: 'the amount is large enough to need a person to approve it',
  recovery_probability_below_threshold: 'the chance of recovering it did not justify contacting the customer',
  escalation_ceiling: 'this case has already been escalated as far as you allow',
  no_action_permitted_by_intervention_table: 'no permitted recovery action remained',
};

export const policyReason = (rule?: string | null) =>
  rule ? (POLICY_REASON[rule] ?? stopReasonLabel(rule)) : null;

/** What each pipeline stage means, for the case timeline. */
export const STAGE_MEANING: Record<string, string> = {
  detection: 'Revenue at risk was identified.',
  diagnosis: 'Revora assessed the situation and worked out the likely cause.',
  decision: 'Revora chose the appropriate recovery action.',
  policy: 'The action was checked against your limits.',
  execution: 'The recovery action was carried out.',
  verification: 'The result was confirmed.',
  recovery: 'The money was recovered.',
  escalation: 'The case was handed to a person.',
};

export const stageMeaning = (stage?: string | null) =>
  stage ? (STAGE_MEANING[stage] ?? '') : '';

/** Review reasons, translated out of engine vocabulary. */
export function reviewReasonLabel(reason: string): string {
  if (reason.toLowerCase().includes('ml') || reason.toLowerCase().includes('disagree')) {
    return 'Revora was not certain enough to act on its own';
  }
  if (reason.toLowerCase().includes('confidently') || reason.toLowerCase().includes('confidence')) {
    return 'The cause could not be determined confidently';
  }
  return reason;
}

/* --------------------------------------------------------------------------
 * Promise to Pay
 * -------------------------------------------------------------------------- */

export const PROMISE_STATUS_LABEL: Record<string, string> = {
  promised: 'Promised',
  due_soon: 'Due soon',
  fulfilled: 'Fulfilled',
  overdue: 'Overdue',
  cancelled: 'Cancelled',
};

export const promiseStatusLabel = (v?: string | null) =>
  lookup(PROMISE_STATUS_LABEL, v);

/** What each promise state means, and what happens next. */
export const PROMISE_STATUS_MEANING: Record<string, string> = {
  promised: 'The customer has told you when they expect to pay. Recovery is paused until then.',
  due_soon: 'The promised date is close. Payment will be verified when it arrives.',
  fulfilled: 'The payment arrived and was verified.',
  overdue: 'The promised date has passed and the payment has not been verified.',
  cancelled: 'The promise was withdrawn. No payment is expected.',
};

export const promiseStatusMeaning = (v?: string | null) =>
  v ? (PROMISE_STATUS_MEANING[v] ?? '') : '';

/* --------------------------------------------------------------------------
 * Communications
 * -------------------------------------------------------------------------- */

export const CHANNEL_CONTACT_LABEL: Record<string, string> = {
  email: 'Recovery email',
  sms: 'Recovery text message',
  voice_script: 'Recovery voice call',
  in_app: 'In-app recovery message',
};

export const contactLabel = (v?: string | null) => lookup(CHANNEL_CONTACT_LABEL, v);

/**
 * Communication status, in words that cannot be mistaken for delivery.
 *
 * "Demo sent" rather than "Sent", because nothing left the building. A merchant
 * reading "Sent" would reasonably conclude a customer had heard from them.
 */
export const COMMUNICATION_STATUS_LABEL: Record<string, string> = {
  prepared: 'Prepared',
  simulated: 'Demo sent',
  blocked: 'Held back by policy',
};

export const communicationStatusLabel = (v?: string | null) =>
  lookup(COMMUNICATION_STATUS_LABEL, v);

export const COMMUNICATION_STATUS_MEANING: Record<string, string> = {
  prepared: 'Written and checked. Nothing has been sent.',
  simulated: 'The demo represented sending this. No customer was contacted.',
  blocked: 'Your policy did not allow this message, so none was written.',
};

export const communicationStatusMeaning = (v?: string | null) =>
  v ? (COMMUNICATION_STATUS_MEANING[v] ?? '') : '';

export const CUSTOMER_RESPONSE_LABEL: Record<string, string> = {
  promised_to_pay: 'Customer response simulated — promised to pay',
  paid: 'Customer response simulated — paid',
  no_response: 'Customer response simulated — no reply',
};

export const customerResponseLabel = (v?: string | null) =>
  lookup(CUSTOMER_RESPONSE_LABEL, v);

/** What a merchant should do next with this contact. */
export function communicationNextStep(
  status: string,
  response: string | null,
  promiseId: string | null,
): string {
  if (status === 'blocked') return 'No further action. The message was not written.';
  if (status === 'prepared') return 'Send it in the demo to continue the conversation.';
  if (promiseId) return 'A promise to pay was recorded. Recovery waits for that date.';
  if (response === 'paid') return 'Payment reported. Verify it against the case.';
  if (response === 'no_response') return 'No reply. Revora may try again within your limits.';
  return 'Simulate how the customer replied.';
}

/* --------------------------------------------------------------------------
 * Humanising engine text
 * --------------------------------------------------------------------------
 * The engine records its own vocabulary — `cash_flow_delay`, `no_action`,
 * `hard_stop_cause` — because an audit trail has to be precise and stable.
 * That precision is exactly what a merchant should never be shown.
 *
 * Rather than rewrite what the engine stores, these helpers translate on the
 * way out. The database keeps the identifiers an auditor needs; the screen
 * shows the words a person reads.
 */

/** Every enum map, searched in order of specificity. */
const ALL_MAPS: Array<Record<string, string>> = [
  ROOT_CAUSE_LABEL,
  ACTION_LABEL,
  STOP_REASON_LABEL,
  OUTCOME_LABEL,
  EVENT_TYPE_LABEL,
  CHANNEL_LABEL,
  AUDIT_ACTION_LABEL,
  STAGE_LABEL,
  COMPLIANCE_RULE_LABEL,
  POLICY_STATUS_LABEL,
  PROMISE_STATUS_LABEL,
  COMMUNICATION_STATUS_LABEL,
  CUSTOMER_RESPONSE_LABEL,
];

/** Statuses a case can be in, in words. */
export const EVENT_STATUS_LABEL: Record<string, string> = {
  detected: 'Detected',
  diagnosing: 'Being diagnosed',
  intervening: 'Being worked',
  recovered: 'Recovered',
  unrecoverable: 'Written off',
  escalated: 'With a person',
  stopped: 'Stopped',
  awaiting_approval: 'Awaiting approval',
};

export const eventStatusLabel = (v?: string | null) => lookup(EVENT_STATUS_LABEL, v);

/**
 * Translate a single engine value, whatever kind it is.
 *
 * Tries every known map before falling back to sentence case, so an identifier
 * nobody has mapped yet still reads as words rather than as code.
 */
export function humanValue(value?: string | null): string {
  if (!value) return '—';
  const key = value.trim();
  if (!key) return '—';
  const found = ALL_MAPS.find((map) => key in map);
  return found ? found[key] : toSentence(key);
}

/**
 * Translate every engine identifier embedded in a sentence.
 *
 * The engine writes prose with identifiers inside it — "Policy gate blocked
 * every candidate: hard_stop_cause". Replacing each token in place keeps the
 * sentence intact while removing the vocabulary a merchant cannot read.
 */
export function humanSentence(text?: string | null): string {
  if (!text) return '';
  return text.replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g, (token) => {
    const mapped = ALL_MAPS.find((map) => token in map);
    return (mapped ? mapped[token] : toSentence(token)).toLowerCase();
  });
}
