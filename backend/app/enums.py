"""Central enum vocabulary for every status/type field in Revora.

BUILD_SPEC Section 15, session 1: "enums for all status/type fields".

Every enum here subclasses ``str`` so members compare equal to their wire value
(``EventStatus.OPEN == "open"``), which keeps JSON serialisation, Pydantic
schemas and SQL storage all speaking the same lowercase snake_case vocabulary
used throughout the spec.

Nothing in this module contains business logic. Which root cause triggers which
action lives in engine/diagnosis_engine.py and engine/policy_engine.py
(session 3); this file only fixes the vocabulary those engines are allowed to
use, so ten separate build sessions cannot drift into inventing new strings.
"""

from __future__ import annotations

from enum import Enum


class EventType(str, Enum):
    """The 5 core risk event types. Section 4 — no others may be added.

    Note the three "capabilities on top of the same engine" (Section 1) that are
    deliberately NOT event types:
      * B2B receivables -> ``INVOICE_OVERDUE`` carrying ``channel=b2b`` in
        ``RiskEvent.raw_signal``.
      * Promise-to-Pay  -> the PromiseToPay entity attached to an
        ``INVOICE_OVERDUE`` / ``SUBSCRIPTION_FAILED`` event; a broken promise
        re-enters as a NEW event with root cause ``BROKEN_PTP``.
      * Hinglish voice  -> an execution channel (``Channel.VOICE_SCRIPT``).
    """

    PAYMENT_DEGRADED = "payment_degraded"
    CHECKOUT_ABANDONED = "checkout_abandoned"
    SUBSCRIPTION_FAILED = "subscription_failed"
    INVOICE_OVERDUE = "invoice_overdue"
    MANDATE_FAILED = "mandate_failed"


class EventStatus(str, Enum):
    """Lifecycle status of a RiskEvent. Section 8 state machine.

    Semantics (enforced by engine/state_machine.py):
      OPEN          detected, not yet reasoned about.
      DIAGNOSING    root-cause classification in flight.
      INTERVENING   at least one recovery action executed or in flight.
      RECOVERED     money actually recovered. TERMINAL.
      ESCALATED     handed to a human. The engine stops auto-acting, but the
                    commercial outcome is still open, so this is NOT terminal —
                    a human collecting the money moves it to RECOVERED.
      UNRECOVERABLE definitively lost / exhausted. TERMINAL.
      STOPPED       the engine deliberately declined to act further (cooldown,
                    do-not-contact, max attempts, hard decline, policy block).
                    Not terminal: a stopped event can still be escalated, or be
                    settled externally by the customer.
    """

    OPEN = "open"
    DIAGNOSING = "diagnosing"
    INTERVENING = "intervening"
    RECOVERED = "recovered"
    ESCALATED = "escalated"
    UNRECOVERABLE = "unrecoverable"
    STOPPED = "stopped"


class GatewayUsed(str, Enum):
    """Which payment gateway executed (or will execute) against this record.

    Section 5: user-selectable at runtime via the UI toggle. Recorded on both
    RiskEvent and PaymentAttempt so the audit trail shows which gateway a given
    attempt actually hit.
    """

    LOCAL_SIMULATION = "local_simulation"
    RAZORPAY_TEST = "razorpay_test"


class PaymentAttemptStatus(str, Enum):
    """Outcome of a single execution attempt. Section 4."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


class RootCauseCode(str, Enum):
    """The complete root-cause vocabulary from the Section 6 table.

    ``BROKEN_PTP`` is the Section 4 promise-tracker cause: when a PromiseToPay
    lapses, the watcher raises a NEW event carrying this cause.
    """

    # --- payment_degraded ---
    CARD_EXPIRED = "card_expired"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    ISSUER_DECLINED = "issuer_declined"
    NETWORK_TIMEOUT = "network_timeout"
    THREE_DS_FAILED = "3ds_failed"
    BANK_SERVER_DOWN = "bank_server_down"
    RISK_ENGINE_BLOCKED = "risk_engine_blocked"

    # --- checkout_abandoned ---
    PAYMENT_STEP_DROPPED = "payment_step_dropped"
    OTP_TIMEOUT = "otp_timeout"
    PRICE_SHOCK = "price_shock"
    NO_PREFERRED_METHOD = "no_preferred_method"
    SESSION_EXPIRED = "session_expired"
    UNKNOWN = "unknown"

    # --- subscription_failed ---
    MANDATE_REVOKED = "mandate_revoked"
    USER_PAUSED = "user_paused"
    HALTED_AFTER_MAX_RETRIES = "halted_after_max_retries"

    # --- invoice_overdue (incl. B2B) ---
    FORGOTTEN = "forgotten"
    DISPUTED_AMOUNT = "disputed_amount"
    AWAITING_APPROVAL = "awaiting_approval"
    CASH_FLOW_DELAY = "cash_flow_delay"
    DELIVERY_FAILURE = "delivery_failure"
    BROKEN_PTP = "broken_ptp"

    # --- mandate_failed ---
    NOT_AUTHENTICATED = "not_authenticated"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    BANK_REJECTED = "bank_rejected"
    EXPIRED = "expired"
    REVOKED = "revoked"


#: Which root causes are legal for which event type (Section 6 table, read row
#: by row). The diagnosis engine (session 3) must not emit a cause outside its
#: event type's set; the ML classifier's label space is derived from this too.
ROOT_CAUSES_BY_EVENT_TYPE: dict[EventType, frozenset[RootCauseCode]] = {
    EventType.PAYMENT_DEGRADED: frozenset(
        {
            RootCauseCode.CARD_EXPIRED,
            RootCauseCode.INSUFFICIENT_FUNDS,
            RootCauseCode.ISSUER_DECLINED,
            RootCauseCode.NETWORK_TIMEOUT,
            RootCauseCode.THREE_DS_FAILED,
            RootCauseCode.BANK_SERVER_DOWN,
            RootCauseCode.RISK_ENGINE_BLOCKED,
        }
    ),
    EventType.CHECKOUT_ABANDONED: frozenset(
        {
            RootCauseCode.PAYMENT_STEP_DROPPED,
            RootCauseCode.OTP_TIMEOUT,
            RootCauseCode.PRICE_SHOCK,
            RootCauseCode.NO_PREFERRED_METHOD,
            RootCauseCode.SESSION_EXPIRED,
            RootCauseCode.UNKNOWN,
        }
    ),
    EventType.SUBSCRIPTION_FAILED: frozenset(
        {
            RootCauseCode.CARD_EXPIRED,
            RootCauseCode.INSUFFICIENT_FUNDS,
            RootCauseCode.MANDATE_REVOKED,
            RootCauseCode.USER_PAUSED,
            RootCauseCode.HALTED_AFTER_MAX_RETRIES,
        }
    ),
    EventType.INVOICE_OVERDUE: frozenset(
        {
            RootCauseCode.FORGOTTEN,
            RootCauseCode.DISPUTED_AMOUNT,
            RootCauseCode.AWAITING_APPROVAL,
            RootCauseCode.CASH_FLOW_DELAY,
            RootCauseCode.DELIVERY_FAILURE,
            RootCauseCode.BROKEN_PTP,
        }
    ),
    EventType.MANDATE_FAILED: frozenset(
        {
            RootCauseCode.NOT_AUTHENTICATED,
            RootCauseCode.INSUFFICIENT_BALANCE,
            RootCauseCode.BANK_REJECTED,
            RootCauseCode.EXPIRED,
            RootCauseCode.REVOKED,
        }
    ),
}


class ProbabilitySource(str, Enum):
    """Which engine produced ``Decision.recovery_probability``. Section 4.

    ``DETERMINISTIC`` is the P0 lookup-table engine; ``ML_P1`` is the optional
    P1 logistic-regression scorer, toggleable against it.
    """

    DETERMINISTIC = "deterministic"
    ML_P1 = "ml_p1"


class PolicyResultStatus(str, Enum):
    """Outcome of the policy gate, stored inside ``Decision.policy_result``."""

    ALLOWED = "allowed"
    BLOCKED = "blocked"


class Channel(str, Enum):
    """Execution / contact channels.

    ``VOICE_SCRIPT`` is the Hinglish recovery channel — an execution channel,
    not an event type (Section 4). ``B2B`` is the ``raw_signal`` flag value that
    marks an ``INVOICE_OVERDUE`` event as a B2B receivable.
    """

    EMAIL = "email"
    SMS = "sms"
    IN_APP = "in_app"
    WHATSAPP = "whatsapp"
    VOICE_SCRIPT = "voice_script"
    HUMAN_HANDOFF = "human_handoff"
    B2B = "b2b"
    EXTERNAL = "external"


class OutcomeResolution(str, Enum):
    """Terminal commercial result recorded on the recovery ledger. Section 4."""

    RECOVERED = "recovered"
    PARTIALLY_RECOVERED = "partially_recovered"
    LOST = "lost"
    PENDING = "pending"


class PromiseStatus(str, Enum):
    """Promise-to-Pay lifecycle. Section 4 — the daily watcher flips
    PENDING -> KEPT or PENDING -> BROKEN."""

    PENDING = "pending"
    KEPT = "kept"
    BROKEN = "broken"
    #: The customer or merchant withdrew the promise before its date. Distinct
    #: from BROKEN: nothing was owed on it, so it must never become a recovery.
    CANCELLED = "cancelled"


class AuditActor(str, Enum):
    """Who performed an audited action. Section 4 AuditLog."""

    SYSTEM = "system"
    HUMAN = "human"


class AuditStage(str, Enum):
    """The pipeline stage an audit entry belongs to. Section 4 AuditLog.

    These are exactly the stages named in the Section 2 bar:
    detection -> diagnosis -> decision -> policy -> execution -> verification
    -> recovery/escalation.
    """

    DETECTION = "detection"
    DIAGNOSIS = "diagnosis"
    DECISION = "decision"
    POLICY = "policy"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    RECOVERY = "recovery"
    ESCALATION = "escalation"


class CommunicationStatus(str, Enum):
    """What has actually happened to a recovery message.

    There is deliberately no "sent" or "delivered". Revora has no provider
    integration, so claiming either would be a lie a merchant could act on.
    SIMULATED means the demo represented a send; nobody was contacted.
    """

    #: Written and compliance-checked, but not yet put through the demo send.
    PREPARED = "prepared"
    #: The demo represented sending it. No customer was contacted.
    SIMULATED = "simulated"
    #: Compliance refused it, so no message text exists at all.
    BLOCKED = "blocked"


class CustomerResponse(str, Enum):
    """What a simulated customer did about a recovery message.

    Simulated in the demo, never observed. Nothing here may be inferred from a
    message having been prepared or sent — a customer who has not answered has
    simply not answered.
    """

    PROMISED_TO_PAY = "promised_to_pay"
    PAID = "paid"
    NO_RESPONSE = "no_response"
