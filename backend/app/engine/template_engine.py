"""Template engine. BUILD_SPEC Section 7.

    "Reasoning + Hinglish scripts are generated from structured
     decision_factors via a template engine (Jinja/YAML), NOT an LLM."

No model, no external API, no generated sentence anywhere in this file. Every
sentence Revora shows a human lives in app/templates/*.yaml; this module loads
them, fills named slots from recorded state, and enforces the four compliance
rules before returning anything.

Compliance is a gate, not a warning
-----------------------------------
A script that fails any rule is NOT rendered. There is no "generate it but flag
it" path, because a rendered string is a string somebody can copy and send. The
four rules from Section 7:

1. contact-time restriction  — 08:00-19:00 IST, on IST local time
2. no coercive language      — blocklist applied to the RENDERED output
3. frequency cap             — attempts_used vs contact_limit_per_channel,
                               checked BEFORE rendering
4. no false urgency          — urgency bounded by the escalation level actually
                               reached

Every check is recorded on the result whether it passed or failed, so the UI can
show what was verified rather than asking anyone to take it on trust.

Slot filling
------------
Templates use ``{named}`` slots filled from ``Decision.decision_factors`` and the
event. A missing slot raises rather than rendering the word "None" at a customer;
:class:`TemplateRenderError` is caught by the caller and surfaced as a failure.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("revora.templates")

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

REASONING_FILE = TEMPLATE_DIR / "reasoning_templates.yaml"
SCRIPT_FILE = TEMPLATE_DIR / "hinglish_script_templates.yaml"
COMPLIANCE_FILE = TEMPLATE_DIR / "compliance_rules.yaml"

#: India Standard Time. Fixed offset — India observes no DST, so this is exact
#: and needs no tzdata package in the container.
IST = timezone(timedelta(hours=5, minutes=30), "IST")

TONES = ("friendly", "neutral", "formal")
URGENCIES = ("low", "medium", "high")


class TemplateError(RuntimeError):
    """Base for anything that goes wrong rendering a template."""


class TemplateRenderError(TemplateError):
    """A template referenced a slot that was not available."""


class ComplianceFailure(TemplateError):
    """A compliance rule refused the request. Carries the machine-readable rule."""

    def __init__(self, rule_id: str, message: str) -> None:
        self.rule_id = rule_id
        super().__init__(message)


@dataclass(frozen=True)
class ComplianceCheck:
    """One rule, its verdict, and why."""

    rule_id: str
    description: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ScriptResult:
    """A generated script, plus everything needed to defend it."""

    event_id: str
    script: str
    reasoning: str
    tone: str
    urgency: str
    channel: str
    language: str
    compliant: bool
    compliance_checks: list[ComplianceCheck]
    slots_used: dict[str, Any] = field(default_factory=dict)
    template_key: str = ""
    failure_reason: str | None = None


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


@functools.lru_cache(maxsize=1)
def load_templates() -> dict[str, Any]:
    """Load and cache all three YAML files.

    Cached because they are configuration read on every script request and they
    do not change at runtime. ``load_templates.cache_clear()`` in a test that
    needs to reload.
    """
    bundle: dict[str, Any] = {}
    for name, path in (
        ("reasoning", REASONING_FILE),
        ("scripts", SCRIPT_FILE),
        ("compliance", COMPLIANCE_FILE),
    ):
        try:
            with open(path, encoding="utf-8") as handle:
                bundle[name] = yaml.safe_load(handle)
        except FileNotFoundError as exc:
            raise TemplateError(
                f"Template file missing: {path}. Scripts cannot be generated "
                "without it — Revora does not fall back to hardcoded text."
            ) from exc
        except yaml.YAMLError as exc:
            raise TemplateError(f"Template file {path} is not valid YAML: {exc}") from exc

        if not isinstance(bundle[name], dict):
            raise TemplateError(f"Template file {path} did not parse to a mapping.")
    return bundle


# --------------------------------------------------------------------------- #
# Slot filling
# --------------------------------------------------------------------------- #


def render(template: str, slots: dict[str, Any]) -> str:
    """Fill ``{named}`` slots, failing loudly on a missing one.

    ``str.format_map`` with a plain dict raises KeyError on an absent slot,
    which is the behaviour we want: rendering "None" or "{amount}" at a customer
    is worse than returning nothing.
    """
    try:
        return " ".join(template.format_map(slots).split())
    except KeyError as exc:
        raise TemplateRenderError(
            f"Template referenced slot {exc} which was not available in the "
            "recorded decision factors."
        ) from exc
    except (IndexError, ValueError) as exc:
        raise TemplateRenderError(f"Malformed template: {exc}") from exc


def build_slots(
    *,
    event: Any,
    decision: Any | None,
    diagnosis: Any | None,
    customer_name: str,
    merchant_name: str,
) -> dict[str, Any]:
    """Assemble every slot a template may reference, from recorded state only.

    Nothing here is computed for presentation — each value is read from the
    event, the diagnosis, or ``Decision.decision_factors``, which is what the
    engine actually reasoned over.
    """
    factors: dict[str, Any] = (
        decision.decision_factors if decision and isinstance(decision.decision_factors, dict) else {}
    )
    policy_result: dict[str, Any] = (
        decision.policy_result if decision and isinstance(decision.policy_result, dict) else {}
    )

    event_type = event.type.value if hasattr(event.type, "value") else str(event.type)
    root_cause = (
        diagnosis.root_cause_code.value
        if diagnosis is not None
        else str(factors.get("root_cause", "unknown"))
    )
    probability = float(decision.recovery_probability) if decision else 0.0
    action_code = decision.action_code if decision else "no_action"

    return {
        "customer_name": customer_name,
        "merchant_name": merchant_name,
        "amount": f"{Decimal(str(event.amount)):,.2f}",
        "currency": event.currency,
        "event_type": event_type,
        "event_type_label": event_type.replace("_", " "),
        "root_cause": root_cause.replace("_", " "),
        "root_cause_code": root_cause,
        "days_overdue": factors.get("days_overdue") or 0,
        "attempt_number": factors.get("attempt_number", 1),
        "action_code": action_code,
        "action_label": action_code.replace("_", " "),
        "probability_pct": f"{probability * 100:.0f}%",
        "confidence_pct": (
            f"{diagnosis.confidence * 100:.0f}%" if diagnosis is not None else "0%"
        ),
        "rule_triggered": str(policy_result.get("rule_triggered") or "none"),
        "threshold_checked": str(policy_result.get("threshold_checked") or "none"),
        "actual_value": str(policy_result.get("actual_value") or "none"),
        "threshold_value": str(policy_result.get("threshold_value") or "none"),
    }


# --------------------------------------------------------------------------- #
# Tone and urgency — derived, never chosen
# --------------------------------------------------------------------------- #


def select_tone(
    *, escalation_level: int, customer_success_rate: float | None, root_cause: str
) -> str:
    """Pick a tone from real state.

    Section 7: "tone/urgency selected by root cause + escalation level +
    customer history". A customer who normally pays gets the benefit of the
    doubt; an escalated or disputed case does not get chirpy.
    """
    if escalation_level >= 2:
        return "formal"
    if root_cause in ("disputed_amount", "broken_ptp", "bank_rejected", "issuer_declined"):
        return "formal"
    if escalation_level >= 1:
        return "neutral"
    if customer_success_rate is not None and customer_success_rate >= 0.75:
        return "friendly"
    return "neutral"


def select_urgency(*, escalation_level: int, days_overdue: int = 0) -> str:
    """Urgency follows escalation level, bounded by it.

    Deliberately cannot exceed what rule 4 permits: the ceiling is derived from
    the same table the compliance check reads, so the two can never disagree.
    """
    ceiling = permitted_urgency(escalation_level)
    proposed = "low"
    if escalation_level >= 2 or days_overdue > 45:
        proposed = "high"
    elif escalation_level >= 1 or days_overdue > 14:
        proposed = "medium"
    return min(proposed, ceiling, key=lambda value: URGENCIES.index(value))


def permitted_urgency(escalation_level: int) -> str:
    """Highest urgency rule 4 allows at this escalation level."""
    rules = load_templates()["compliance"]["urgency"]
    table = rules["permitted_by_escalation_level"]
    key = max((k for k in table if int(k) <= escalation_level), default=0)
    return str(table[key])


# --------------------------------------------------------------------------- #
# Compliance — Section 7's four rules
# --------------------------------------------------------------------------- #


def check_contact_window(now: datetime | None = None) -> ComplianceCheck:
    """Rule 1: 08:00-19:00 IST."""
    rules = load_templates()["compliance"]["contact_window"]
    moment = (now or datetime.now(timezone.utc)).astimezone(IST)
    hour = moment.hour
    ok = rules["start_hour"] <= hour < rules["end_hour"]
    return ComplianceCheck(
        rule_id=rules["rule_id"],
        description=rules["description"],
        passed=ok,
        detail=(
            f"{moment.strftime('%H:%M')} IST is within "
            f"{rules['start_hour']:02d}:00-{rules['end_hour']:02d}:00."
            if ok
            else f"{moment.strftime('%H:%M')} IST is outside "
            f"{rules['start_hour']:02d}:00-{rules['end_hour']:02d}:00. "
            f"{rules['failure_message']}"
        ),
    )


def check_language(text: str) -> ComplianceCheck:
    """Rule 2: no coercive or threatening language, checked on rendered output."""
    rules = load_templates()["compliance"]["language"]
    haystack = text.lower()
    for phrase in rules.get("allowlist_phrases", []):
        haystack = haystack.replace(str(phrase).lower(), "")
    hits = [term for term in rules["blocklist"] if str(term).lower() in haystack]
    return ComplianceCheck(
        rule_id=rules["rule_id"],
        description=rules["description"],
        passed=not hits,
        detail=(
            "No prohibited language found in the rendered script."
            if not hits
            else f"{rules['failure_message']} Terms found: {', '.join(hits)}."
        ),
    )


def check_frequency_cap(*, attempts_used: int, contact_limit: int) -> ComplianceCheck:
    """Rule 3: attempts_used vs Policy.contact_limit_per_channel, before rendering."""
    rules = load_templates()["compliance"]["frequency"]
    ok = attempts_used < contact_limit
    return ComplianceCheck(
        rule_id=rules["rule_id"],
        description=rules["description"],
        passed=ok,
        detail=(
            f"{attempts_used} of {contact_limit} permitted contacts used."
            if ok
            else f"{rules['failure_message']} attempts_used={attempts_used}, "
            f"contact_limit_per_channel={contact_limit}."
        ),
    )


def check_urgency(*, urgency: str, escalation_level: int) -> ComplianceCheck:
    """Rule 4: urgency must match the escalation level actually reached."""
    rules = load_templates()["compliance"]["urgency"]
    ranks = rules["urgency_rank"]
    ceiling = permitted_urgency(escalation_level)
    ok = int(ranks[urgency]) <= int(ranks[ceiling])
    return ComplianceCheck(
        rule_id=rules["rule_id"],
        description=rules["description"],
        passed=ok,
        detail=(
            f"Urgency '{urgency}' is permitted at escalation level L{escalation_level} "
            f"(ceiling '{ceiling}')."
            if ok
            else f"{rules['failure_message']} Requested '{urgency}' at L{escalation_level}, "
            f"which permits at most '{ceiling}'."
        ),
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def render_reasoning(slots: dict[str, Any], *, policy_status: str) -> tuple[str, str]:
    """Reasoning sentence for a decision. Returns ``(text, template_key)``.

    Lookup is most-specific-first: root cause, then event type, then the
    default — so a cause with something particular to say says it, and
    everything else still gets a correct sentence.
    """
    templates = load_templates()["reasoning"]

    sentences = templates["policy_sentences"]
    policy_key = "blocked" if policy_status == "blocked" else "allowed"
    if slots.get("action_code") == "no_action" and policy_status != "blocked":
        policy_key = "no_action"
    slots = {**slots, "policy_sentence": render(sentences[policy_key], slots)}

    cause = slots["root_cause_code"]
    event_type = slots["event_type"]

    if cause in templates.get("by_root_cause", {}):
        return render(templates["by_root_cause"][cause], slots), f"by_root_cause.{cause}"
    if event_type in templates.get("by_event_type", {}):
        return (
            render(templates["by_event_type"][event_type], slots),
            f"by_event_type.{event_type}",
        )
    return render(templates["default"], slots), "default"


def render_script(slots: dict[str, Any], *, tone: str, urgency: str) -> tuple[str, str]:
    """Hinglish script body. Returns ``(text, template_key)``."""
    templates = load_templates()["scripts"]
    if tone not in TONES:
        raise TemplateRenderError(f"Unknown tone {tone!r}; expected one of {TONES}.")

    cause = slots["root_cause_code"]
    by_cause = templates.get("by_root_cause", {})
    if cause in by_cause and tone in by_cause[cause]:
        body_template = by_cause[cause][tone]
        key = f"by_root_cause.{cause}.{tone}"
    else:
        body_template = templates["default"][tone]
        key = f"default.{tone}"

    parts = [
        render(templates["greetings"][tone], slots),
        render(body_template, slots),
    ]
    urgency_line = templates["urgency_lines"].get(urgency, "")
    if urgency_line:
        parts.append(render(urgency_line, slots))
    parts.append(render(templates["closings"][tone], slots))

    return " ".join(part for part in parts if part), key


def generate_script(
    *,
    event: Any,
    decision: Any | None,
    diagnosis: Any | None,
    stopping_state: Any | None,
    policy: Any,
    customer: Any | None,
    channel: str = "voice_script",
    now: datetime | None = None,
) -> ScriptResult:
    """Generate a compliance-checked Hinglish script for one event.

    The order matters. The frequency cap is evaluated BEFORE any text is
    produced, so an over-cap event never yields a string that could be sent by
    mistake. The language check runs AFTER rendering, because a template is only
    safe or unsafe once its slots are filled.

    Returns a :class:`ScriptResult` in both outcomes. On failure ``script`` is
    empty, ``compliant`` is False, and ``compliance_checks`` records exactly
    which rule refused and why — the caller shows that rather than a script.
    """
    escalation_level = int(getattr(stopping_state, "escalation_level", 0) or 0)
    attempts_used = int(getattr(stopping_state, "attempts_used", 0) or 0)
    contact_limit = int(getattr(policy, "contact_limit_per_channel", 0) or 0)
    success_rate = getattr(customer, "payment_success_rate", None)
    customer_name = (
        (event.raw_signal or {}).get("customer_name")
        if isinstance(event.raw_signal, dict)
        else None
    ) or event.customer_id

    slots = build_slots(
        event=event,
        decision=decision,
        diagnosis=diagnosis,
        customer_name=str(customer_name),
        merchant_name=getattr(getattr(event, "merchant", None), "name", "your merchant"),
    )

    tone = select_tone(
        escalation_level=escalation_level,
        customer_success_rate=success_rate,
        root_cause=slots["root_cause_code"],
    )
    days_overdue = slots["days_overdue"]
    urgency = select_urgency(
        escalation_level=escalation_level,
        days_overdue=int(days_overdue) if isinstance(days_overdue, int) else 0,
    )

    checks: list[ComplianceCheck] = []

    # --- pre-render gates ---------------------------------------------------
    window = check_contact_window(now)
    checks.append(window)

    frequency = check_frequency_cap(
        attempts_used=attempts_used, contact_limit=contact_limit
    )
    checks.append(frequency)

    urgency_check = check_urgency(urgency=urgency, escalation_level=escalation_level)
    checks.append(urgency_check)

    policy_status = str(
        (decision.policy_result or {}).get("status", "allowed") if decision else "allowed"
    )
    reasoning, _ = render_reasoning(slots, policy_status=policy_status)

    blocking = [check for check in checks if not check.passed]
    if blocking:
        # Nothing is rendered. A script that exists is a script that can be sent.
        return ScriptResult(
            event_id=event.id,
            script="",
            reasoning=reasoning,
            tone=tone,
            urgency=urgency,
            channel=channel,
            language="hinglish",
            compliant=False,
            compliance_checks=checks,
            slots_used=slots,
            failure_reason=blocking[0].detail,
        )

    script, template_key = render_script(slots, tone=tone, urgency=urgency)

    # --- post-render gate ---------------------------------------------------
    language = check_language(f"{script} {reasoning}")
    checks.append(language)

    if not language.passed:
        return ScriptResult(
            event_id=event.id,
            script="",
            reasoning=reasoning,
            tone=tone,
            urgency=urgency,
            channel=channel,
            language="hinglish",
            compliant=False,
            compliance_checks=checks,
            slots_used=slots,
            template_key=template_key,
            failure_reason=language.detail,
        )

    return ScriptResult(
        event_id=event.id,
        script=script,
        reasoning=reasoning,
        tone=tone,
        urgency=urgency,
        channel=channel,
        language="hinglish",
        compliant=True,
        compliance_checks=checks,
        slots_used=slots,
        template_key=template_key,
    )
