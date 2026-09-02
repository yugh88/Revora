"""LangGraph orchestration of the recovery workflow.

WHAT THIS ADDS, AND WHAT IT DOES NOT
------------------------------------
It adds a declared shape. The recovery sequence already existed and already
worked; it was expressed as control flow inside ``run_batch``, where the branches
— blocked, retry, stopped, promised, error — were readable only by following the
code. Here the same sequence is a graph, so the states and the transitions
between them are something you can look at.

It adds no intelligence whatsoever. Every node delegates to the engine that
already owns that step:

    diagnose  -> diagnosis_engine        decide  -> decision_engine
    gate      -> policy_engine           script  -> template_engine (compliance)
    context   -> retrieval               respond -> promise_tracker

There is no LLM in this graph's decision path. The language layer is reached
only from the ``script`` node, and only to rewrite wording that the compliance
gate has already approved. A node that let a model choose an action would make
this an agent, and an agent is precisely what a recovery system handling other
people's money should not be.

WHY NOT REPLACE run_batch
-------------------------
Because ``run_batch`` is covered by a large test suite that encodes real
behaviour learned over many sessions, and rewriting it to gain a diagram would
risk correctness for presentation. This runs the same engines over one event and
reports the path taken. It is orchestration and introspection, not a second
pipeline — and the ledger writes stay where they always were.

LangChain is not used. ``langgraph`` depends on ``langchain-core`` for its type
plumbing, which is unavoidable, but no chain, agent, tool or prompt abstraction
from LangChain appears anywhere in this file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, TypedDict

from sqlalchemy.orm import Session

from app.database import utcnow
from app.enums import EventStatus
from app.models import Diagnosis, RiskEvent, StoppingRuleState

logger = logging.getLogger("revora.graph")

#: Terminal outcomes a case can reach in one pass through the graph.
RecoveryState = Literal[
    "diagnosed",
    "blocked",
    "stopped",
    "approved",
    "communicated",
    "promised",
    "fulfilled",
    "overdue",
    "error",
]


class GraphState(TypedDict, total=False):
    """The minimal state carried between nodes.

    Deliberately small and flat. Anything not needed to choose the next branch
    belongs in the database, which is where the rest of the case already lives —
    a fat state object would become a second, competing model of the case.
    """

    event_id: str
    customer_id: str
    root_cause: str | None
    action: str | None
    policy_status: str | None
    policy_rule: str | None
    channel: str | None
    has_context: bool
    script_compliant: bool | None
    promise_id: str | None
    state: RecoveryState
    path: list[str]
    error: str | None


@dataclass
class GraphResult:
    """What one pass through the graph did."""

    state: RecoveryState
    path: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def _node(name: str, state: GraphState) -> GraphState:
    """Record that a node ran, so the path is inspectable afterwards."""
    state.setdefault("path", []).append(name)
    return state


# --------------------------------------------------------------------------- #
# Nodes. Each one delegates; none decides.
# --------------------------------------------------------------------------- #


def build_graph(session: Session, *, now: datetime | None = None):
    """Compile the recovery graph.

    Bound to a session at construction time because every node reads the
    database, and threading a session through LangGraph's state would put a
    live connection into a structure meant to be serialisable.
    """
    from langgraph.graph import END, StateGraph

    moment = now or utcnow()

    def diagnose(state: GraphState) -> GraphState:
        _node("diagnose", state)
        event = session.get(RiskEvent, state["event_id"])
        stored = session.get(Diagnosis, state["event_id"]) if event else None
        state["root_cause"] = stored.root_cause_code.value if stored else None
        state["customer_id"] = event.customer_id if event else ""
        state["state"] = "diagnosed"
        return state

    def decide(state: GraphState) -> GraphState:
        """Read the decision the deterministic engine already recorded."""
        from sqlalchemy import select

        from app.models import Decision

        _node("decide", state)
        decision = session.execute(
            select(Decision)
            .where(Decision.event_id == state["event_id"])
            .order_by(Decision.decided_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        state["action"] = decision.action_code if decision else None
        if decision is not None and isinstance(decision.policy_result, dict):
            state["policy_status"] = decision.policy_result.get("status")
            state["policy_rule"] = decision.policy_result.get("rule_triggered")
        return state

    def gate(state: GraphState) -> GraphState:
        """The policy engine's verdict, taken as final.

        Nothing downstream may soften this. A blocked case leaves the graph
        blocked, and no context, model or retry logic gets a vote.
        """
        _node("gate", state)
        stopping = session.get(StoppingRuleState, state["event_id"])
        if stopping is not None and stopping.hard_stop_reason:
            state["state"] = "stopped"
        elif state.get("policy_status") == "blocked":
            state["state"] = "blocked"
        else:
            state["state"] = "approved"
        return state

    def context(state: GraphState) -> GraphState:
        """Attach retrieved history. Context only — it changes no branch."""
        from app.engine.retrieval import retrieve_context

        _node("context", state)
        event = session.get(RiskEvent, state["event_id"])
        if event is None:
            state["has_context"] = False
            return state
        retrieved = retrieve_context(session, event, now=moment)
        state["has_context"] = not retrieved.empty
        return state

    def script(state: GraphState) -> GraphState:
        """Whether an approved, compliance-checked message exists.

        Read from the communication the pipeline already recorded rather than
        generated here: generating a second one would mean a second compliance
        evaluation, and two answers to "may we contact this person?".
        """
        from sqlalchemy import select

        from app.enums import CommunicationStatus
        from app.models import CommunicationLog

        _node("script", state)
        record = session.execute(
            select(CommunicationLog)
            .where(CommunicationLog.event_id == state["event_id"])
            .order_by(CommunicationLog.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if record is None:
            state["script_compliant"] = None
            return state
        state["channel"] = record.channel.value
        state["script_compliant"] = record.status != CommunicationStatus.BLOCKED
        if record.status != CommunicationStatus.BLOCKED:
            state["state"] = "communicated"
        return state

    def respond(state: GraphState) -> GraphState:
        """Where the customer's reply left the case."""
        from sqlalchemy import select

        from app.engine.promise_tracker import display_status
        from app.models import PromiseToPay

        _node("respond", state)
        promise = session.execute(
            select(PromiseToPay)
            .where(PromiseToPay.event_id == state["event_id"])
            .order_by(PromiseToPay.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if promise is None:
            return state

        state["promise_id"] = promise.id
        shown = display_status(promise, now=moment)
        if shown == "fulfilled":
            state["state"] = "fulfilled"
        elif shown == "overdue":
            state["state"] = "overdue"
        else:
            state["state"] = "promised"
        return state

    def after_gate(state: GraphState) -> str:
        """Blocked and stopped cases leave immediately. No second chances."""
        if state.get("state") in ("blocked", "stopped"):
            return "finish"
        return "context"

    def after_script(state: GraphState) -> str:
        return "respond" if state.get("script_compliant") else "finish"

    graph = StateGraph(GraphState)
    graph.add_node("diagnose", diagnose)
    graph.add_node("decide", decide)
    graph.add_node("gate", gate)
    graph.add_node("context", context)
    graph.add_node("script", script)
    graph.add_node("respond", respond)

    graph.set_entry_point("diagnose")
    graph.add_edge("diagnose", "decide")
    graph.add_edge("decide", "gate")
    graph.add_conditional_edges("gate", after_gate, {"context": "context", "finish": END})
    graph.add_edge("context", "script")
    graph.add_conditional_edges("script", after_script, {"respond": "respond", "finish": END})
    graph.add_edge("respond", END)
    return graph.compile()


def run_for_event(
    session: Session, event_id: str, *, now: datetime | None = None
) -> GraphResult:
    """Walk one case through the graph and report where it ended up.

    Read-only. This inspects and orchestrates state the pipeline has already
    produced; it writes nothing, so running it can never change a recovery
    outcome or move money.
    """
    try:
        compiled = build_graph(session, now=now)
        final: GraphState = compiled.invoke(
            {"event_id": event_id, "path": [], "state": "diagnosed"}
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "recovery_graph_failed",
            extra={"event_id": event_id, "stage": "decision", "action": "graph"},
        )
        return GraphResult(state="error", path=[], details={"error": str(exc)})

    return GraphResult(
        state=final.get("state", "error"),
        path=list(final.get("path", [])),
        details={
            "root_cause": final.get("root_cause"),
            "action": final.get("action"),
            "policy_status": final.get("policy_status"),
            "policy_rule": final.get("policy_rule"),
            "channel": final.get("channel"),
            "has_context": final.get("has_context"),
            "promise_id": final.get("promise_id"),
        },
    )
