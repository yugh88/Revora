"""Read schemas for the events feed and drill-down. BUILD_SPEC Sections 10 and 13.

Section 10: ``GET /events``, ``GET /events/{id}`` — "feed + drill-down
(diagnosis, decision, stopping-rule state, audit timeline)". These are the
response shapes for exactly that, and nothing more.

Everything here is READ-ONLY. There is no ingest schema in this file: Section
10 also lists ``POST /events``, but no session has built ingestion and the
drill-down UI does not need it, so inventing a request shape for it now would be
guessing at a contract nobody has designed.

Money is serialised as an exact decimal STRING, matching schemas/batch.py. The
ledger stores integer paise; a JSON number would arrive in JavaScript as a float
and start drifting the moment anything summed it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.enums import EventStatus, EventType, GatewayUsed


class EventSummary(BaseModel):
    """One row of the feed.

    Carries just enough to triage without opening the event: what it is, what
    it is worth, where it got to, and whether a human needs to look at it.
    """

    id: str
    type: EventType
    merchant_id: str
    customer_id: str
    #: The customer's name as it arrived on the inbound signal. The merchant UI
    #: shows people, not identifiers — "Aditya Desai", never "cust_0011".
    #: Falls back to the id only when the signal carried no name.
    customer_name: str
    amount: str
    currency: str
    source_ref: str | None
    detected_at: str
    status: EventStatus
    gateway_used: GatewayUsed
    correlation_id: str

    # --- diagnosis, when the pipeline has reached it ---
    root_cause: str | None = None
    confidence: float | None = None

    # --- latest decision, when one exists ---
    action_code: str | None = None
    recovery_probability: float | None = None
    policy_status: str | None = None

    # --- ML, an independent signal only (Section 4a) ---
    ml_agrees: bool | None = None

    #: True when the rule diagnosis was low-confidence or ML disagreed. This is
    #: the "needs attention" flag the feed sorts and filters on; it is derived
    #: from stored state, never guessed.
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)

    #: Ledger outcome, when settled.
    resolved: str | None = None
    amount_recovered: str | None = None


class EventMoneySummary(BaseModel):
    """Authoritative money totals over the filtered event set.

    Computed server-side from the same tables /batch reads: RiskEvent.amount for
    what is at risk, Outcome.amount_recovered for what came back. The dashboard
    consumes these rather than summing a page of rows in the browser, so there
    is exactly one implementation of "recovery rate" in the product.
    """

    amount_at_risk: str
    amount_recovered: str
    amount_lost: str
    amount_pending: str
    currency: str = "INR"
    recovery_rate: float
    #: Annualised recurring revenue retained through verified recovery.
    #:
    #: Only recurring cases contribute, and only at the cadence recorded on the
    #: event: a monthly charge recovered is worth twelve of itself a year, an
    #: annual one is worth exactly itself. A one-off invoice contributes
    #: nothing, because there is no recurrence to annualise.
    arr_retained: str
    #: Events the engine is still working: intervening + escalated.
    active_interventions: int


class EventListResponse(BaseModel):
    total: int
    returned: int
    limit: int
    offset: int
    #: Counts over the FILTERED set, before pagination — so the summary
    #: describes what the caller asked about, not the current page.
    status_breakdown: dict[str, int]
    type_breakdown: dict[str, int]
    needs_review_count: int
    money: EventMoneySummary
    #: Oldest detection time in the WHOLE ledger, ignoring filters. Lets the UI
    #: say "only N months of history are available" honestly instead of drawing
    #: an empty twelve-month chart.
    earliest_detected_at: str | None = None
    items: list[EventSummary]


class DiagnosisDetail(BaseModel):
    root_cause: str
    confidence: float
    evidence: list[Any]
    diagnosed_at: str
    is_low_confidence: bool


class MlDetail(BaseModel):
    """The classifier's independent opinion.

    Section 4a is explicit that the rule engine stays authoritative for the
    action actually taken. This block exists so a reviewer can see where the two
    disagreed — it never describes what the engine did.
    """

    predicted_root_cause: str
    confidence: float
    agrees_with_rule_engine: bool
    model_version: str
    predicted_at: str


class DecisionDetail(BaseModel):
    id: int
    action_code: str
    recovery_probability: float
    probability_source: str
    policy_result: dict[str, Any]
    policy_version: int
    decision_factors: dict[str, Any]
    reasoning_text: str
    decided_at: str


class StoppingRuleDetail(BaseModel):
    attempts_used: int
    max_attempts_for_type: int
    cooldown_until: str | None
    do_not_contact_snapshot: bool
    escalation_level: int
    hard_stop_reason: str | None


class AttemptDetail(BaseModel):
    id: str
    attempt_number: int
    status: str
    failure_reason: str | None
    provider_ref: str | None
    gateway_used: str
    initiated_at: str
    resolved_at: str | None


class OutcomeDetail(BaseModel):
    resolved: str
    amount_recovered: str
    resolved_at: str | None
    resolution_channel: str | None


class AuditEntry(BaseModel):
    id: int
    timestamp: str
    stage: str
    action: str
    actor: str
    before_state: Any = None
    after_state: Any = None
    reasoning: str | None = None


class EventDetailResponse(BaseModel):
    """Everything recorded about one event.

    ``stages_missing`` is deliberately part of the contract. An event stopped at
    the policy gate SHOULD have no execution or verification entries, and saying
    so explicitly is what lets the UI render "execution not reached because
    policy blocked the action" instead of an ambiguous gap.
    """

    event: EventSummary
    diagnosis: DiagnosisDetail | None
    ml: MlDetail | None
    decisions: list[DecisionDetail]
    stopping_rule_state: StoppingRuleDetail | None
    attempts: list[AttemptDetail]
    outcome: OutcomeDetail | None
    audit: list[AuditEntry]
    stages_present: list[str]
    stages_missing: list[str]
