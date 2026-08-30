"""Promise-to-Pay request/response schemas. BUILD_SPEC Sections 4 and 10.

The wire format speaks merchant, not database. ``status`` carries the DERIVED
state a person should see — promised, due soon, fulfilled, overdue, cancelled —
so the frontend does not have to reimplement the date arithmetic and reach a
different answer.

Money is an exact decimal string, as everywhere else.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.enums import EventType


class PromiseOut(BaseModel):
    """One promise, as a merchant sees it."""

    id: str
    customer_name: str
    promised_amount: str
    currency: str
    promised_date: str
    created_at: str
    resolved_at: str | None

    #: Derived: promised | due_soon | fulfilled | overdue | cancelled.
    status: str

    #: The recovery case that prompted the promise.
    event_id: str
    event_type: EventType
    amount_at_risk: str

    #: What the customer actually said, verbatim. A promise has to be
    #: defensible: "why does Revora think they said 3 September?" needs an
    #: answer that a parsed date alone cannot give.
    source_response: str | None = None

    #: What Revora will do next, in plain language.
    next_step: str = ""

    #: True once the ledger records this case as recovered. Read back from the
    #: ledger rather than inferred from the promise, so the two cannot disagree.
    recovered: bool
    amount_recovered: str


class PromiseListResponse(BaseModel):
    total: int
    #: Counts by derived status, over the whole list rather than a page.
    status_breakdown: dict[str, int]
    total_promised: str
    total_fulfilled: str
    items: list[PromiseOut]


class PromiseCreate(BaseModel):
    """A customer committing to pay by a date."""

    event_id: str = Field(min_length=1)
    promised_amount: Decimal = Field(gt=0)
    promised_date: datetime


class PromiseFulfil(BaseModel):
    """Confirmation that the promised payment arrived.

    ``paid_amount`` is optional; absent, the promised amount is used. It exists
    so a partial payment can be recorded honestly rather than rounded up to the
    promise.
    """

    paid_amount: Decimal | None = Field(default=None, gt=0)
