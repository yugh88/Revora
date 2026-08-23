"""SQLAlchemy engine, session factory, declarative Base and shared column types.

BUILD_SPEC Section 3: SQLite, file-persisted via a Docker volume. Section 12:
this module owns the engine/session wiring.

Two custom column types live here because they eliminate bug classes that would
otherwise surface much later, in the parts of the spec that must be
demonstrably correct:

* ``Money``  — money is stored as an INTEGER count of paise, never a float.
  Section 2's bar demands "real numbers from ledger state"; float rupees would
  make batch totals drift by fractions of a paisa across 500 records. Python
  code deals in ``Decimal`` rupees throughout; the conversion is invisible.

* ``TZDateTime`` — SQLite has no timezone type. This stores UTC and hands back
  timezone-AWARE UTC datetimes, and refuses to write a naive datetime at all.
  Without it, comparing ``ActionLock.expires_at`` or ``PromiseToPay.
  promised_date`` against ``datetime.now(timezone.utc)`` raises
  "can't compare offset-naive and offset-aware datetimes" at runtime. The spec
  wants IST-flavoured synthetic data (Section 11) — that is a *display*
  concern; storage is always UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum as PyEnum
from typing import Any, Iterator

from sqlalchemy import DateTime, Enum as SAEnum, Integer, TypeDecorator, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

# --------------------------------------------------------------------------- #
# Declarative base
# --------------------------------------------------------------------------- #


class Base(DeclarativeBase):
    """Declarative base for every ORM model in app/models/."""


# --------------------------------------------------------------------------- #
# Shared column types
# --------------------------------------------------------------------------- #

_PAISE = Decimal("100")
_TWO_PLACES = Decimal("0.01")


class Money(TypeDecorator):
    """Exact money column: Decimal rupees in Python, integer paise in SQLite.

    Accepts ``Decimal``, ``int``, ``str`` or ``float`` on write (floats are
    routed through ``str`` so that 0.1 + 0.2 style artefacts never reach the
    database) and always returns a 2-dp ``Decimal`` on read.
    """

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> int | None:
        if value is None:
            return None
        rupees = Decimal(str(value)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        return int(rupees * _PAISE)

    def process_result_value(self, value: Any, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        return (Decimal(int(value)) / _PAISE).quantize(_TWO_PLACES)


class TZDateTime(TypeDecorator):
    """Timezone-aware UTC datetime column that is safe on SQLite.

    Raises on naive datetimes rather than silently guessing a timezone — a loud
    failure at write time is far cheaper than a wrong ``cooldown_until``
    comparison in the middle of a 500-record batch run.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"TZDateTime expected datetime, got {type(value).__name__}")
        if value.tzinfo is None:
            raise ValueError(
                "Naive datetime rejected. Revora stores UTC and compares against "
                "timezone-aware values — use app.database.utcnow() or attach a "
                "tzinfo before persisting."
            )
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    """Timezone-aware current UTC time. The only clock the backend should use."""
    return datetime.now(timezone.utc)


def sa_enum(python_enum: type[PyEnum], name: str) -> SAEnum:
    """Build a SQLAlchemy Enum that stores VALUES, not member NAMES.

    Without ``values_callable`` SQLAlchemy would persist ``"PAYMENT_DEGRADED"``
    while the spec, the API contract and the frontend all say
    ``"payment_degraded"``. On SQLite this renders as VARCHAR + a CHECK
    constraint, so an out-of-vocabulary status is rejected by the database
    itself, not merely by application code.

    ``create_constraint=True`` is set explicitly: SQLAlchemy has defaulted it to
    False since 1.4, so without it the CHECK is silently never emitted and a raw
    UPDATE could park an event in a status the state machine has never heard of.
    """
    return SAEnum(
        python_enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


# --------------------------------------------------------------------------- #
# Engine / session
# --------------------------------------------------------------------------- #

_settings = get_settings()

_is_sqlite = _settings.database_url.startswith("sqlite")

engine: Engine = create_engine(
    _settings.database_url,
    # SQLite + FastAPI: requests may touch the connection from a different
    # thread than the one that created it.
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@event.listens_for(Engine, "connect")
def _enforce_sqlite_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
    """Turn on foreign-key enforcement for every SQLite connection.

    SQLite ships with FK enforcement OFF. Without this the FK relationships
    declared across app/models/ would be documentation rather than constraints,
    and an orphaned Decision or AuditLog row would insert silently.
    """
    if not _is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session and always closing it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Create every table declared on ``Base.metadata``.

    Importing ``app.models`` first is what registers the mappers; without that
    import ``create_all`` would create nothing.
    """
    import app.models  # noqa: F401  (import for side effect: mapper registration)

    Base.metadata.create_all(bind=engine)
