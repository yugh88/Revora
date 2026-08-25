"""RiskEvent state machine. BUILD_SPEC Section 8.

    open -> diagnosing -> intervening -> { recovered | escalated | unrecoverable | stopped }

``recovered`` and ``unrecoverable`` are TERMINAL: any attempted transition out of
them must be rejected AND logged to AuditLog as a caught anomaly. Both halves of
that sentence are implemented here — rejecting without logging would fail the
Section 2 bar just as surely as not rejecting at all.

This module is the ONLY place permitted to assign ``RiskEvent.status``. Every
status change anywhere in the codebase goes through :func:`transition`, which is
what makes the audit trail complete by construction rather than by discipline.

Transitions beyond the spec's linear sketch, and why each exists
----------------------------------------------------------------
The spec's arrow diagram is the happy path; a real run needs a few more edges,
all of which are consequences of requirements stated elsewhere in the spec:

* ``intervening -> intervening`` — Section 6 defines attempt 1 AND attempt 2 for
  most event types. The event stays in ``intervening`` across attempts. Modelled
  as an explicit self-edge so a second attempt is an audited event rather than
  something callers have to route around.
* ``open -> stopped`` — a ``do_not_contact`` customer, or a hard-decline signal
  present at detection, is stopped before any reasoning spend.
* ``diagnosing -> stopped`` / ``diagnosing -> unrecoverable`` — Section 6:
  ``issuer_declined`` and ``bank_rejected`` are "no retry, immediate stop".
* ``diagnosing -> recovered`` — Section 9's pre-execution re-check fires after
  diagnosis and before any action; an event found already paid upstream settles
  straight to recovered without the engine ever acting.
* ``stopped -> recovered`` — Section 9's ``recovered_externally`` case: the
  customer paid on their own. The engine correctly declined to act; the money
  still arrived, and the ledger must say so.
* ``stopped -> escalated`` — a human may pick up an auto-stopped event.
* ``escalated -> recovered | unrecoverable | stopped`` — ``escalated`` means the
  engine handed off, not that the outcome is settled, so the human's result has
  to be recordable.

``escalated`` and ``stopped`` are therefore deliberately NOT terminal. Only the
two states the spec names are.

Transaction boundaries
----------------------
:func:`transition` never commits. It mutates the event, appends the AuditLog row
and flushes, leaving the commit to the caller who owns the unit of work
(``/batch`` commits per record, which is also what gives Section 9's fault
isolation its boundary).

One consequence matters and is easy to miss: when a transition is REJECTED, the
anomaly audit row is flushed but not committed. If your exception handler rolls
back, the anomaly disappears with it. Callers that catch
:class:`InvalidTransition` must commit before re-raising or continuing::

    try:
        transition(session, event, EventStatus.INTERVENING, reasoning="retry")
    except InvalidTransition:
        session.commit()   # keep the anomaly record
        raise
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.enums import AuditActor, AuditStage, EventStatus
from app.models.audit_log import AuditLog
from app.models.risk_event import RiskEvent

# --------------------------------------------------------------------------- #
# The graph
# --------------------------------------------------------------------------- #

#: Statuses out of which no transition is ever legal (Section 8).
TERMINAL_STATES: frozenset[EventStatus] = frozenset(
    {EventStatus.RECOVERED, EventStatus.UNRECOVERABLE}
)

#: Complete transition table. Every EventStatus MUST appear as a key — the test
#: suite asserts this, so adding a status without deciding its edges fails loudly.
ALLOWED_TRANSITIONS: dict[EventStatus, frozenset[EventStatus]] = {
    EventStatus.OPEN: frozenset(
        {
            EventStatus.DIAGNOSING,
            EventStatus.STOPPED,
        }
    ),
    EventStatus.DIAGNOSING: frozenset(
        {
            EventStatus.INTERVENING,
            EventStatus.ESCALATED,
            EventStatus.STOPPED,
            EventStatus.UNRECOVERABLE,
            # Section 9's pre-execution re-check runs AFTER diagnosis and BEFORE
            # any action: "If already resolved/cancelled/paid externally -> stop
            # immediately, log recovered_externally". An event discovered to be
            # already paid at that moment is in `diagnosing`, so without this
            # edge the recovered_externally path is unreachable and every
            # externally-settled event raises instead of settling. Session 1
            # added `stopped -> recovered` for the same case but missed that the
            # re-check fires a step earlier. Found by the Session 4 batch run.
            EventStatus.RECOVERED,
        }
    ),
    EventStatus.INTERVENING: frozenset(
        {
            EventStatus.INTERVENING,  # attempt N -> attempt N+1, Section 6
            EventStatus.RECOVERED,
            EventStatus.ESCALATED,
            EventStatus.STOPPED,
            EventStatus.UNRECOVERABLE,
        }
    ),
    EventStatus.ESCALATED: frozenset(
        {
            EventStatus.RECOVERED,
            EventStatus.UNRECOVERABLE,
            EventStatus.STOPPED,
        }
    ),
    EventStatus.STOPPED: frozenset(
        {
            EventStatus.RECOVERED,  # recovered_externally, Section 9
            EventStatus.ESCALATED,
            EventStatus.UNRECOVERABLE,
        }
    ),
    EventStatus.RECOVERED: frozenset(),  # TERMINAL
    EventStatus.UNRECOVERABLE: frozenset(),  # TERMINAL
}

#: Default audit stage for a transition, keyed by the status being entered.
#: Callers may override via ``transition(..., stage=...)``.
DEFAULT_STAGE_FOR_STATUS: dict[EventStatus, AuditStage] = {
    EventStatus.OPEN: AuditStage.DETECTION,
    EventStatus.DIAGNOSING: AuditStage.DIAGNOSIS,
    EventStatus.INTERVENING: AuditStage.EXECUTION,
    EventStatus.RECOVERED: AuditStage.RECOVERY,
    EventStatus.ESCALATED: AuditStage.ESCALATION,
    EventStatus.UNRECOVERABLE: AuditStage.VERIFICATION,
    EventStatus.STOPPED: AuditStage.POLICY,
}

ACTION_STATE_TRANSITION = "state_transition"
ACTION_INVALID_TRANSITION = "invalid_transition_rejected"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class InvalidTransition(Exception):
    """Raised when a status change is not permitted by ALLOWED_TRANSITIONS."""

    def __init__(
        self,
        from_status: EventStatus,
        to_status: EventStatus,
        *,
        event_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.from_status = from_status
        self.to_status = to_status
        self.event_id = event_id
        self.detail = detail or (
            f"{from_status.value} -> {to_status.value} is not an allowed transition"
        )
        location = f" (event_id={event_id})" if event_id else ""
        super().__init__(f"{self.detail}{location}")


class TerminalStateViolation(InvalidTransition):
    """Raised specifically when leaving a terminal state was attempted.

    A distinct type because this is the anomaly Section 8 calls out by name, and
    /exceptions should be able to surface it separately from an ordinary
    out-of-order transition.
    """

    def __init__(
        self,
        from_status: EventStatus,
        to_status: EventStatus,
        *,
        event_id: str | None = None,
    ) -> None:
        super().__init__(
            from_status,
            to_status,
            event_id=event_id,
            detail=(
                f"{from_status.value} is a terminal state; "
                f"transition to {to_status.value} rejected"
            ),
        )


# --------------------------------------------------------------------------- #
# Pure predicates (no session, no side effects)
# --------------------------------------------------------------------------- #


def _coerce(status: EventStatus | str) -> EventStatus:
    """Accept an EventStatus or its wire value; reject anything else clearly."""
    if isinstance(status, EventStatus):
        return status
    try:
        return EventStatus(status)
    except ValueError as exc:
        valid = ", ".join(sorted(member.value for member in EventStatus))
        raise ValueError(f"Unknown event status {status!r}. Valid values: {valid}") from exc


def is_terminal(status: EventStatus | str) -> bool:
    """True when no transition out of ``status`` is ever permitted."""
    return _coerce(status) in TERMINAL_STATES


def allowed_next_states(status: EventStatus | str) -> frozenset[EventStatus]:
    """Statuses reachable in one step from ``status``."""
    return ALLOWED_TRANSITIONS[_coerce(status)]


def can_transition(from_status: EventStatus | str, to_status: EventStatus | str) -> bool:
    """True when the transition is permitted. Never raises for known statuses."""
    return _coerce(to_status) in ALLOWED_TRANSITIONS[_coerce(from_status)]


def validate_transition(
    from_status: EventStatus | str,
    to_status: EventStatus | str,
    *,
    event_id: str | None = None,
) -> None:
    """Raise if the transition is illegal, otherwise return None.

    Raises:
        TerminalStateViolation: leaving ``recovered`` or ``unrecoverable``.
        InvalidTransition: any other disallowed edge.
        ValueError: unknown status string.
    """
    frm = _coerce(from_status)
    to = _coerce(to_status)

    if frm in TERMINAL_STATES:
        raise TerminalStateViolation(frm, to, event_id=event_id)
    if to not in ALLOWED_TRANSITIONS[frm]:
        raise InvalidTransition(frm, to, event_id=event_id)


def reachable_states(start: EventStatus = EventStatus.OPEN) -> set[EventStatus]:
    """Every status reachable from ``start`` by any path.

    Used by the test suite to prove the graph has no orphaned state — a status
    nothing can ever reach would be dead code hiding in the schema.
    """
    seen: set[EventStatus] = {start}
    frontier: list[EventStatus] = [start]
    while frontier:
        current = frontier.pop()
        for nxt in ALLOWED_TRANSITIONS[current]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen


# --------------------------------------------------------------------------- #
# The audited transition
# --------------------------------------------------------------------------- #


def _append_audit(
    session: Session,
    *,
    event: RiskEvent,
    stage: AuditStage,
    action: str,
    before_state: Any,
    after_state: Any,
    reasoning: str,
    actor: AuditActor,
    correlation_id: str | None,
) -> AuditLog:
    entry = AuditLog(
        event_id=event.id,
        correlation_id=correlation_id or event.correlation_id,
        actor=actor,
        stage=stage,
        action=action,
        before_state=before_state,
        after_state=after_state,
        reasoning=reasoning,
    )
    session.add(entry)
    session.flush()
    return entry


def transition(
    session: Session,
    event: RiskEvent,
    to_status: EventStatus | str,
    *,
    reasoning: str,
    actor: AuditActor = AuditActor.SYSTEM,
    stage: AuditStage | None = None,
    action: str = ACTION_STATE_TRANSITION,
    correlation_id: str | None = None,
) -> RiskEvent:
    """Move ``event`` to ``to_status``, writing an audit entry either way.

    On success the event's status is updated and a ``state_transition`` entry is
    appended. On rejection an ``invalid_transition_rejected`` entry is appended
    recording the ATTEMPTED target, and the exception is re-raised — the caught
    anomaly Section 8 requires.

    Does not commit; see the module docstring on transaction boundaries.

    Args:
        session: Active SQLAlchemy session owning ``event``.
        event: The RiskEvent to transition.
        to_status: Target status, as an EventStatus or its wire value.
        reasoning: Why this transition is happening. Required — an audit trail
            of bare status changes with no justification would not meet the bar.
        actor: ``system`` (default) or ``human`` for manual intervention.
        stage: Overrides the default stage derived from ``to_status``.
        action: Overrides the audit action verb on success.
        correlation_id: Overrides the event's own correlation id.

    Returns:
        The same ``event``, mutated.

    Raises:
        TerminalStateViolation: attempted exit from a terminal state.
        InvalidTransition: any other disallowed edge.
        ValueError: unknown status string.
    """
    frm = _coerce(event.status)
    to = _coerce(to_status)
    resolved_stage = stage or DEFAULT_STAGE_FOR_STATUS[to]

    try:
        validate_transition(frm, to, event_id=event.id)
    except InvalidTransition as exc:
        _append_audit(
            session,
            event=event,
            stage=resolved_stage,
            action=ACTION_INVALID_TRANSITION,
            before_state=frm.value,
            after_state=frm.value,  # unchanged: the transition did NOT happen
            reasoning=(
                f"ANOMALY: rejected transition to {to.value!r}. {exc.detail}. "
                f"Requested by {actor.value}: {reasoning}"
            ),
            actor=actor,
            correlation_id=correlation_id,
        )
        raise

    event.status = to
    _append_audit(
        session,
        event=event,
        stage=resolved_stage,
        action=action,
        before_state=frm.value,
        after_state=to.value,
        reasoning=reasoning,
        actor=actor,
        correlation_id=correlation_id,
    )
    return event


def transition_many(
    session: Session,
    events: Iterable[RiskEvent],
    to_status: EventStatus | str,
    *,
    reasoning: str,
    actor: AuditActor = AuditActor.SYSTEM,
) -> tuple[list[RiskEvent], list[tuple[RiskEvent, InvalidTransition]]]:
    """Transition several events, isolating failures per record.

    Mirrors Section 9's batch fault-isolation rule at the state-machine level:
    one bad record is caught and reported, the rest still move.

    Returns:
        ``(succeeded, failures)`` where ``failures`` pairs each event with its
        exception. Anomaly audit rows are appended for the failures.
    """
    succeeded: list[RiskEvent] = []
    failures: list[tuple[RiskEvent, InvalidTransition]] = []
    for event in events:
        try:
            transition(session, event, to_status, reasoning=reasoning, actor=actor)
        except InvalidTransition as exc:
            failures.append((event, exc))
        else:
            succeeded.append(event)
    return succeeded, failures
