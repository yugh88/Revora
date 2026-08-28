"""RecoveryRun — a completed analysis, kept so it can be reopened later.

Without this, a finished run existed only in the browser tab that started it.
Navigate away and the result was gone; the only way back was to run the whole
thing again. That is fine for a developer and useless for a merchant who wants
to look at last Tuesday's recovery.

WHAT THIS IS NOT
----------------
It is not a second source of financial truth. The recovery ledger — RiskEvent,
Outcome and the rest — remains authoritative for every rupee. This table stores
what a particular run REPORTED at the moment it finished: a historical
snapshot, the same way a printed statement is a snapshot rather than a rival
account.

That distinction has a practical consequence. These figures are deliberately
NOT recomputed on read. If the ledger later changes, a past run should still
show what it actually reported, because that is what the merchant saw and acted
on. Recomputing would quietly rewrite history.

Money is stored through the same ``Money`` TypeDecorator as everywhere else —
exact integer paise, never a float.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Float, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, Money, TZDateTime, sa_enum
from app.enums import GatewayUsed


class RecoveryRun(Base):
    """One completed recovery analysis, as it reported itself."""

    __tablename__ = "recovery_runs"

    #: The batch id the pipeline already generated. Reusing it means a run
    #: record and its events share one identity, so a run can always be traced
    #: back to the exact rows it created.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    #: Merchant-readable name, e.g. "Morning Recovery Run — 28 Aug". Generated
    #: once at completion and stored, so the name a merchant saw is the name
    #: they see when they come back — it cannot drift with the clock.
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    started_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    gateway: Mapped[GatewayUsed] = mapped_column(sa_enum(GatewayUsed, "gateway_used"), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False, default=42)

    # --- volume ---
    total_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    isolated_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_duplicates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- money, exactly as the run reported it ---
    amount_at_risk: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    amount_recovered: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    amount_pending: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    amount_lost: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    recovery_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # --- headline activity, for the history list ---
    recovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    escalated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: The complete BatchResponse as returned. Stored verbatim so reopening a
    #: run renders through exactly the same presentation as when it finished,
    #: with no second implementation of the metrics to drift out of agreement.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RecoveryRun {self.id} {self.name!r} recovered={self.amount_recovered}>"
