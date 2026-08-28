"""Batch request/response schemas. BUILD_SPEC Section 10.

Section 10 fixes what ``POST /batch`` must return: amount at risk / attempted /
recovered / lost, a recovery rate "from actual ledger state", and the explicit
breakdowns that prove the bar — ``escalation_ceiling_hits``,
``stopping_rule_triggers`` broken down by reason, and the promise counts.

Money crosses the wire as a STRING, not a float. ``Decimal("2499.00")``
serialised as a JSON number would arrive in JavaScript as a float and start
accumulating error the moment the frontend summed anything. Strings keep the
value exact and make the frontend decide how to parse it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.enums import GatewayUsed

#: Section 10: "process N synthetic records (default 50, supports 500)".
DEFAULT_BATCH_SIZE = 50
MAX_BATCH_SIZE = 500


class BatchRequest(BaseModel):
    """Input to ``POST /batch``."""

    count: int = Field(
        default=DEFAULT_BATCH_SIZE,
        ge=1,
        le=MAX_BATCH_SIZE,
        description="Number of synthetic records to process. Default 50, max 500.",
    )
    gateway: GatewayUsed = Field(
        default=GatewayUsed.LOCAL_SIMULATION,
        description=(
            "Which gateway executes. The built-in simulator is the default and "
            "requires no credentials."
        ),
    )
    seed: int = Field(
        default=42,
        description="Synthetic generator seed. Fixed at 42 for reproducibility (Section 11).",
    )


class StoppingRuleTriggers(BaseModel):
    """Section 10 names these four reasons explicitly."""

    cooldown: int = 0
    do_not_contact: int = 0
    max_attempts: int = 0
    hard_decline: int = 0
    #: Reasons the engine can also produce, kept separate so the four the spec
    #: names stay exactly as specified rather than being diluted.
    other: dict[str, int] = Field(default_factory=dict)

    @property
    def total(self) -> int:
        return (
            self.cooldown
            + self.do_not_contact
            + self.max_attempts
            + self.hard_decline
            + sum(self.other.values())
        )


class BatchMoney(BaseModel):
    """The money view of a batch, every figure read back from the ledger."""

    amount_at_risk: str
    amount_attempted: str
    amount_recovered: str
    amount_lost: str
    amount_pending: str
    currency: str = "INR"


class IsolatedFailure(BaseModel):
    """One record that failed without taking the batch down. Section 9."""

    record_index: int
    event_id: str | None = None
    correlation_id: str | None = None
    stage: str
    error_type: str
    error_message: str


class BatchResponse(BaseModel):
    """Result of a batch run.

    Every count and every amount here is queried back from the database after
    the run completes, never accumulated in a Python counter during the loop.
    That is deliberate: it makes the response impossible to inflate independently
    of what actually happened, which is what Section 2's "real numbers from
    ledger state, not invented" requires.
    """

    batch_id: str
    correlation_id: str
    gateway: GatewayUsed
    seed: int
    started_at: str
    finished_at: str
    duration_seconds: float

    # --- volume ---
    total_records: int
    processed: int
    isolated_failures: int
    skipped_duplicates: int

    # --- money, from the ledger ---
    money: BatchMoney
    recovery_rate: float = Field(
        description="amount_recovered / amount_at_risk, from ledger state. 0.0 when nothing at risk."
    )
    resolution_rate: float = Field(
        description=(
            "Share of processed events reaching a resolved status. Section 11: "
            "100% here is a red flag, not a win."
        )
    )

    # --- the breakdowns Section 10 names ---
    escalation_ceiling_hits: int
    stopping_rule_triggers: StoppingRuleTriggers
    promises_made: int
    promises_kept: int
    promises_broken: int

    # --- pipeline detail ---
    status_breakdown: dict[str, int]
    action_breakdown: dict[str, int]
    event_type_breakdown: dict[str, int]
    outcome_breakdown: dict[str, int]

    # --- hybrid ML layer, Section 4a ---
    ml_agreement_rate: float | None = Field(
        default=None,
        description=(
            "Measured agreement between classifier and rule engine. None when no "
            "ML opinion was available for any event — an absent opinion is NOT a "
            "disagreement."
        ),
    )
    ml_predictions: int = 0
    ml_agreements: int = 0
    ml_unavailable: int = 0

    # --- review queue ---
    exceptions_raised: int
    audit_entries: int

    failures: list[IsolatedFailure] = Field(default_factory=list)


class ExceptionItem(BaseModel):
    """One row of ``GET /exceptions``. Section 10.

    Answers "why did the engine not act, or why does a human need to look?" in
    a form a judge can read without opening the database.
    """

    event_id: str
    event_type: str
    amount: str
    currency: str
    status: str
    reason: str
    reason_code: str
    stage: str
    rule_triggered: str | None = None
    threshold_checked: str | None = None
    actual_value: Any = None
    threshold_value: Any = None
    action_code: str | None = None
    rule_root_cause: str | None = None
    rule_confidence: float | None = None
    ml_root_cause: str | None = None
    ml_confidence: float | None = None
    ml_agrees: bool | None = None
    correlation_id: str
    detected_at: str
    occurred_at: str


class ExceptionsResponse(BaseModel):
    total: int
    returned: int
    reason_breakdown: dict[str, int]
    items: list[ExceptionItem]


class AuditItem(BaseModel):
    """One immutable audit entry. Section 4."""

    id: int
    timestamp: str
    event_id: str | None
    correlation_id: str
    actor: str
    stage: str
    action: str
    before_state: Any = None
    after_state: Any = None
    reasoning: str | None = None


class AuditResponse(BaseModel):
    total: int
    returned: int
    offset: int
    limit: int
    stage_breakdown: dict[str, int]
    items: list[AuditItem]


class AuditTrailResponse(BaseModel):
    """One event's full pipeline trail, in order.

    This is the view that lets a judge follow detection -> diagnosis -> decision
    -> policy -> execution -> verification -> recovery/escalation for a single
    event without filtering the whole log by hand.
    """

    event_id: str
    correlation_id: str
    stages_present: list[str]
    stages_missing: list[str]
    entry_count: int
    items: list[AuditItem]


AuditSortOrder = Literal["asc", "desc"]


class RunSummary(BaseModel):
    """One completed run, as it reported itself.

    A historical snapshot, not a recomputation: these are the figures the run
    produced at the time. The recovery ledger stays authoritative for what is
    true now.
    """

    id: str
    name: str
    finished_at: str
    gateway: GatewayUsed
    total_records: int
    processed: int
    amount_at_risk: str
    amount_recovered: str
    amount_pending: str
    amount_lost: str
    recovery_rate: float
    recovered_count: int
    escalated_count: int


class RunListResponse(BaseModel):
    total: int
    items: list[RunSummary]


class RunDetailResponse(BaseModel):
    """A run summary plus the full response it returned when it finished."""

    run: RunSummary
    #: The complete BatchResponse, stored verbatim so reopening a run renders
    #: through exactly the same presentation it did on completion.
    snapshot: dict[str, Any]
