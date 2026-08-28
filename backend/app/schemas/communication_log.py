"""Communication schemas. BUILD_SPEC Sections 7 and 10.

The wire format speaks merchant. ``status`` carries values a person can read,
and there is no field anywhere that could be mistaken for delivery evidence —
because there is none.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.enums import Channel, CustomerResponse, EventType


class CommunicationOut(BaseModel):
    """One recovery message, as a merchant sees it."""

    id: str
    customer_name: str
    channel: Channel
    status: str
    #: Empty when compliance refused. Never partially rendered.
    body: str
    reason: str
    blocked_reason: str | None

    #: Always true. There is no provider integration behind any of this.
    is_simulated: bool

    created_at: str
    simulated_at: str | None
    customer_response: CustomerResponse | None
    responded_at: str | None
    promise_id: str | None

    event_id: str
    event_type: EventType
    amount_at_risk: str


class CommunicationListResponse(BaseModel):
    total: int
    channel_breakdown: dict[str, int]
    status_breakdown: dict[str, int]
    items: list[CommunicationOut]


class CommunicationPrepare(BaseModel):
    """Ask Revora to write the message it would send for a case."""

    event_id: str = Field(min_length=1)
    #: Omit to let Revora choose from the action it decided on.
    channel: Channel | None = None


class SimulatedResponse(BaseModel):
    """What a simulated customer did.

    A commitment to pay needs a date; the amount defaults to the whole balance,
    because that is what a customer promising to settle usually means.
    """

    response: CustomerResponse
    promised_date: datetime | None = None
    promised_amount: Decimal | None = Field(default=None, gt=0)
