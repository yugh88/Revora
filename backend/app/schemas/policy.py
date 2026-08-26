"""Policy request/response schemas. BUILD_SPEC Sections 4 and 10.

Policies are VERSIONED, never mutated. ``PUT /policies`` inserts a new row with
an incremented ``policy_version`` for that (merchant, event_type) pair and
leaves the previous row alone, because ``Decision.policy_version`` pins every
past decision to the policy that actually gated it. Editing a threshold today
must not silently rewrite the reasoning behind a decision made yesterday.

Money is an exact decimal string on the wire, as everywhere else.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.enums import EventType


class PolicyOut(BaseModel):
    """One effective policy."""

    policy_version: int
    merchant_id: str
    event_type: EventType
    max_attempts: int
    cooldown_hours: int
    amount_threshold: str
    recovery_probability_threshold: float
    contact_limit_per_channel: int
    escalation_ceiling: int
    updated_at: str | None
    #: True when no merchant row exists and these are the engine's built-in
    #: defaults. The UI says so rather than presenting them as configuration
    #: somebody chose.
    is_default: bool = False


class PolicyListResponse(BaseModel):
    merchant_id: str
    total: int
    items: list[PolicyOut]


class PolicyUpdate(BaseModel):
    """A merchant's new bounds for one event type.

    Validation is deliberately strict and mirrors what the policy engine can act
    on. Section 6 caps auto-escalation at L2, so an escalation ceiling above 2
    is rejected rather than silently clamped — a merchant who sets 5 should be
    told it is not permitted, not quietly given 2.
    """

    merchant_id: str = Field(min_length=1)
    event_type: EventType
    max_attempts: int = Field(ge=0, le=10)
    cooldown_hours: int = Field(ge=0, le=720)
    amount_threshold: Decimal = Field(ge=0)
    recovery_probability_threshold: float = Field(ge=0.0, le=1.0)
    contact_limit_per_channel: int = Field(ge=0, le=10)
    escalation_ceiling: int = Field(ge=0, le=2)

    @field_validator("amount_threshold")
    @classmethod
    def _quantize(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))
