"""Template engine and compliance. BUILD_SPEC Section 7.

    cd backend && PYTHONPATH=. pytest -q tests/test_template_engine.py

Compliance is the reason this file exists. Section 7's four rules are what stop
Revora being a spam cannon, so each is tested BOTH ways — a case it refuses and
a matching case it permits. A rule that only ever passes would satisfy a
one-sided suite and fail the product.

Two properties get particular attention:

* a refused script produces NO TEXT. "Generate it but flag it" would leave a
  string somebody could copy and send.
* urgency cannot exceed the escalation level actually reached, so a first
  friendly reminder can never be dressed up as a final notice.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.engine import template_engine as te
from app.engine.template_engine import (
    IST,
    ComplianceFailure,
    TemplateRenderError,
    check_contact_window,
    check_frequency_cap,
    check_language,
    check_urgency,
    generate_script,
    load_templates,
    permitted_urgency,
    render,
    select_tone,
    select_urgency,
)
from app.enums import EventType, RootCauseCode


# --------------------------------------------------------------------------- #
# Stubs — the engine takes plain objects, so no database is needed
# --------------------------------------------------------------------------- #


class _Merchant:
    name = "Chai Point Retail"


class _Event:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "evt_t1")
        self.type = kwargs.get("type", EventType.PAYMENT_DEGRADED)
        self.amount = kwargs.get("amount", Decimal("2499.00"))
        self.currency = "INR"
        self.customer_id = kwargs.get("customer_id", "cust_t1")
        self.raw_signal = kwargs.get("raw_signal", {"customer_name": "Aarav Sharma"})
        self.merchant = _Merchant()


class _Diagnosis:
    def __init__(self, cause=RootCauseCode.CARD_EXPIRED, confidence=0.95):
        self.root_cause_code = cause
        self.confidence = confidence


class _Decision:
    def __init__(self, action="update_card_email", probability=0.55, policy_status="allowed", factors=None):
        self.action_code = action
        self.recovery_probability = probability
        self.policy_result = {
            "status": policy_status,
            "rule_triggered": None if policy_status == "allowed" else "do_not_contact",
            "threshold_checked": None if policy_status == "allowed" else "customer.do_not_contact",
            "actual_value": None if policy_status == "allowed" else True,
            "threshold_value": None if policy_status == "allowed" else False,
        }
        self.decision_factors = factors or {"attempt_number": 1, "days_overdue": None}


class _State:
    def __init__(self, attempts_used=0, escalation_level=0):
        self.attempts_used = attempts_used
        self.escalation_level = escalation_level


class _Policy:
    def __init__(self, contact_limit=2):
        self.contact_limit_per_channel = contact_limit


class _Customer:
    def __init__(self, rate=0.85):
        self.payment_success_rate = rate


#: Midday IST — inside the permitted contact window.
MIDDAY = datetime(2026, 8, 26, 12, 0, tzinfo=IST).astimezone(timezone.utc)


def build(**overrides):
    """Generate a script with sensible, compliant defaults."""
    kwargs = {
        "event": _Event(),
        "decision": _Decision(),
        "diagnosis": _Diagnosis(),
        "stopping_state": _State(),
        "policy": _Policy(),
        "customer": _Customer(),
        "now": MIDDAY,
    }
    kwargs.update(overrides)
    return generate_script(**kwargs)


# --------------------------------------------------------------------------- #
# YAML loading
# --------------------------------------------------------------------------- #


class TestTemplatesLoad:
    def test_all_three_files_load(self):
        bundle = load_templates()
        assert set(bundle) == {"reasoning", "scripts", "compliance"}

    def test_the_sentences_live_in_yaml_not_in_python(self):
        """Section 7: templates are configuration, not business logic."""
        import inspect

        source = inspect.getsource(te)
        # The engine may name slots and rule ids, but must not contain the
        # customer-facing prose itself.
        for phrase in ("Namaste", "Dhanyavaad", "ji,", "kripya", "Kripya"):
            assert phrase not in source, f"template prose {phrase!r} is hardcoded in Python"

    def test_every_tone_exists_for_greetings_and_closings(self):
        scripts = load_templates()["scripts"]
        for tone in te.TONES:
            assert tone in scripts["greetings"]
            assert tone in scripts["closings"]
            assert tone in scripts["default"]

    def test_every_root_cause_template_covers_all_three_tones(self):
        """A cause with only one tone would silently fall back and lose its
        specificity."""
        for cause, tones in load_templates()["scripts"]["by_root_cause"].items():
            assert set(tones) == set(te.TONES), f"{cause} is missing a tone"

    def test_compliance_file_defines_all_four_rules(self):
        rules = load_templates()["compliance"]
        assert set(rules) >= {"contact_window", "language", "frequency", "urgency"}

    def test_the_contact_window_is_the_spec_window(self):
        """Section 7: 8am-7pm IST."""
        window = load_templates()["compliance"]["contact_window"]
        assert window["start_hour"] == 8
        assert window["end_hour"] == 19

    def test_a_missing_template_file_fails_loudly(self, monkeypatch):
        """Falling back to hardcoded text would defeat the whole design."""
        from pathlib import Path

        load_templates.cache_clear()
        monkeypatch.setattr(te, "REASONING_FILE", Path("/nonexistent/nope.yaml"))
        with pytest.raises(te.TemplateError, match="Template file missing"):
            load_templates()
        load_templates.cache_clear()


class TestRender:
    def test_slots_are_filled(self):
        assert render("Hello {name}", {"name": "Aarav"}) == "Hello Aarav"

    def test_a_missing_slot_raises_rather_than_printing_none(self):
        """Rendering "None" at a customer is worse than rendering nothing."""
        with pytest.raises(TemplateRenderError, match="not available"):
            render("Hello {name}", {})

    def test_whitespace_from_yaml_folding_is_collapsed(self):
        assert render("a\n  b   c", {}) == "a b c"


# --------------------------------------------------------------------------- #
# Tone and urgency are derived, not chosen
# --------------------------------------------------------------------------- #


class TestToneSelection:
    def test_a_reliable_customer_gets_the_friendly_tone(self):
        assert (
            select_tone(escalation_level=0, customer_success_rate=0.9, root_cause="forgotten")
            == "friendly"
        )

    def test_a_weaker_history_gets_neutral(self):
        assert (
            select_tone(escalation_level=0, customer_success_rate=0.4, root_cause="forgotten")
            == "neutral"
        )

    def test_escalation_forces_formal(self):
        assert (
            select_tone(escalation_level=2, customer_success_rate=0.99, root_cause="forgotten")
            == "formal"
        )

    def test_a_disputed_amount_is_never_chirpy(self):
        assert (
            select_tone(
                escalation_level=0, customer_success_rate=0.99, root_cause="disputed_amount"
            )
            == "formal"
        )

    def test_a_broken_promise_is_never_chirpy(self):
        assert (
            select_tone(escalation_level=0, customer_success_rate=0.99, root_cause="broken_ptp")
            == "formal"
        )


class TestUrgencySelection:
    def test_level_zero_is_low(self):
        assert select_urgency(escalation_level=0) == "low"

    def test_level_one_is_medium(self):
        assert select_urgency(escalation_level=1) == "medium"

    def test_level_two_is_high(self):
        assert select_urgency(escalation_level=2) == "high"

    def test_days_overdue_cannot_exceed_the_escalation_ceiling(self):
        """The false-urgency guard: a very old invoice at L0 still cannot be
        phrased as urgent."""
        assert select_urgency(escalation_level=0, days_overdue=400) == "low"

    def test_the_ceiling_comes_from_the_yaml_rule_table(self):
        assert permitted_urgency(0) == "low"
        assert permitted_urgency(1) == "medium"
        assert permitted_urgency(2) == "high"


# --------------------------------------------------------------------------- #
# Rule 1 — contact-time restriction
# --------------------------------------------------------------------------- #


class TestContactWindow:
    @pytest.mark.parametrize("hour", [8, 12, 18])
    def test_inside_the_window_passes(self, hour):
        moment = datetime(2026, 8, 26, hour, 0, tzinfo=IST).astimezone(timezone.utc)
        assert check_contact_window(moment).passed is True

    @pytest.mark.parametrize("hour", [0, 3, 7, 19, 22, 23])
    def test_outside_the_window_fails(self, hour):
        moment = datetime(2026, 8, 26, hour, 0, tzinfo=IST).astimezone(timezone.utc)
        assert check_contact_window(moment).passed is False

    def test_the_window_is_evaluated_in_ist_not_server_time(self):
        """03:00 UTC is 08:30 IST — permitted. A server in UTC must not refuse
        a message that is perfectly reasonable in India."""
        moment = datetime(2026, 8, 26, 3, 0, tzinfo=timezone.utc)
        assert check_contact_window(moment).passed is True

    def test_2am_ist_is_refused_even_though_it_is_daytime_utc(self):
        moment = datetime(2026, 8, 25, 20, 30, tzinfo=timezone.utc)  # 02:00 IST
        assert check_contact_window(moment).passed is False

    def test_an_out_of_hours_request_produces_no_script_at_all(self):
        night = datetime(2026, 8, 26, 2, 0, tzinfo=IST).astimezone(timezone.utc)
        result = build(now=night)
        assert result.compliant is False
        assert result.script == ""
        assert "contact window" in (result.failure_reason or "").lower()


# --------------------------------------------------------------------------- #
# Rule 2 — no coercive language
# --------------------------------------------------------------------------- #


class TestCoerciveLanguage:
    @pytest.mark.parametrize(
        "text",
        [
            "We will take legal action against you",
            "Pay now or we will send a recovery agent",
            "This is your final warning",
            "Your credit score will be affected",
            "We will report you to the authorities",
        ],
    )
    def test_threatening_text_is_blocked(self, text):
        assert check_language(text).passed is False

    def test_ordinary_polite_text_passes(self):
        assert check_language("Namaste, aapka payment pending hai. Dhanyavaad!").passed is True

    def test_the_allowlist_prevents_an_overreach(self):
        """"report you" is coercive; "report a problem" is ordinary support
        language and must not be blocked."""
        assert check_language("You can report a problem any time.").passed is True

    def test_every_shipped_template_passes_its_own_blocklist(self):
        """The templates being careful is not a substitute for the check, but
        they must at least not fail it."""
        scripts = load_templates()["scripts"]
        for tone_map in scripts["by_root_cause"].values():
            for text in tone_map.values():
                assert check_language(text).passed is True, text
        for group in ("greetings", "closings", "default"):
            for text in scripts[group].values():
                assert check_language(text).passed is True, text

    def test_a_coercive_render_yields_no_script(self, monkeypatch):
        """The post-render gate: even if a template were changed to something
        threatening, nothing is returned."""
        monkeypatch.setattr(
            te, "render_script", lambda slots, tone, urgency: ("We will take legal action", "x")
        )
        result = build()
        assert result.compliant is False
        assert result.script == ""


# --------------------------------------------------------------------------- #
# Rule 3 — frequency cap
# --------------------------------------------------------------------------- #


class TestFrequencyCap:
    def test_under_the_cap_passes(self):
        assert check_frequency_cap(attempts_used=1, contact_limit=2).passed is True

    def test_at_the_cap_fails(self):
        assert check_frequency_cap(attempts_used=2, contact_limit=2).passed is False

    def test_over_the_cap_fails(self):
        assert check_frequency_cap(attempts_used=5, contact_limit=2).passed is False

    def test_the_cap_is_checked_before_any_text_is_produced(self):
        """Section 7 says "before generating". An over-cap event must never
        yield a string that could be sent by mistake."""
        result = build(stopping_state=_State(attempts_used=2), policy=_Policy(contact_limit=2))
        assert result.compliant is False
        assert result.script == ""
        assert any(
            c.rule_id == "frequency_cap" and not c.passed for c in result.compliance_checks
        )

    def test_the_merchants_own_limit_is_what_binds(self):
        """A tighter policy must actually tighten."""
        tight = build(stopping_state=_State(attempts_used=1), policy=_Policy(contact_limit=1))
        loose = build(stopping_state=_State(attempts_used=1), policy=_Policy(contact_limit=3))
        assert tight.compliant is False
        assert loose.compliant is True


# --------------------------------------------------------------------------- #
# Rule 4 — no false urgency
# --------------------------------------------------------------------------- #


class TestFalseUrgency:
    def test_high_urgency_at_level_zero_is_refused(self):
        assert check_urgency(urgency="high", escalation_level=0).passed is False

    def test_high_urgency_at_level_two_is_permitted(self):
        assert check_urgency(urgency="high", escalation_level=2).passed is True

    def test_low_urgency_is_always_permitted(self):
        for level in (0, 1, 2):
            assert check_urgency(urgency="low", escalation_level=level).passed is True

    def test_a_first_reminder_never_reads_as_a_final_notice(self):
        result = build(stopping_state=_State(escalation_level=0))
        assert result.urgency == "low"
        assert "jald se jald" not in result.script

    def test_an_escalated_event_may_carry_real_urgency(self):
        result = build(
            stopping_state=_State(escalation_level=2), policy=_Policy(contact_limit=5)
        )
        assert result.urgency == "high"


# --------------------------------------------------------------------------- #
# Generation end to end
# --------------------------------------------------------------------------- #


class TestGeneration:
    def test_a_compliant_request_produces_a_script(self):
        result = build()
        assert result.compliant is True
        assert result.script
        assert result.reasoning

    def test_all_four_rules_are_recorded_on_a_successful_generation(self):
        """Section 7: record which compliance rules were checked."""
        result = build()
        assert {c.rule_id for c in result.compliance_checks} == {
            "contact_time_window",
            "no_coercive_language",
            "frequency_cap",
            "no_false_urgency",
        }
        assert all(c.passed for c in result.compliance_checks)

    def test_checks_are_recorded_on_failure_too(self):
        result = build(stopping_state=_State(attempts_used=9), policy=_Policy(contact_limit=1))
        assert result.compliance_checks
        assert any(not c.passed for c in result.compliance_checks)

    def test_the_script_is_hinglish_not_english(self):
        result = build()
        assert any(word in result.script for word in ("Namaste", "aapka", "kar", "hai"))

    def test_real_values_are_interpolated(self):
        result = build(event=_Event(amount=Decimal("7500.00")))
        assert "7,500.00" in result.script or "7,500.00" in result.reasoning

    def test_the_customer_name_comes_from_the_signal(self):
        result = build(event=_Event(raw_signal={"customer_name": "Meera Iyer"}))
        assert "Meera Iyer" in result.script

    def test_the_template_key_traces_to_a_yaml_entry(self):
        """So the wording can be traced to a file, not to code."""
        result = build(diagnosis=_Diagnosis(RootCauseCode.CARD_EXPIRED))
        assert result.template_key.startswith("by_root_cause.card_expired")

    def test_a_cause_without_its_own_template_falls_back_cleanly(self):
        result = build(diagnosis=_Diagnosis(RootCauseCode.RISK_ENGINE_BLOCKED))
        assert result.compliant is True
        assert result.template_key.startswith("default.")

    def test_the_reasoning_is_produced_even_when_the_script_is_refused(self):
        """A reviewer still needs to know what the engine concluded, even when
        no message may be sent."""
        result = build(stopping_state=_State(attempts_used=9), policy=_Policy(contact_limit=1))
        assert result.script == ""
        assert result.reasoning

    def test_a_blocked_decision_says_so_in_the_reasoning(self):
        result = build(decision=_Decision(policy_status="blocked", action="no_action"))
        assert "blocked" in result.reasoning.lower() or "do not contact" in result.reasoning.lower()

    def test_reasoning_uses_a_cause_specific_template_when_one_exists(self):
        result = build(diagnosis=_Diagnosis(RootCauseCode.CARD_EXPIRED))
        assert "expired" in result.reasoning.lower()

    def test_generation_is_deterministic(self):
        """Same recorded state, same words — there is no model here."""
        first = build()
        second = build()
        assert first.script == second.script
        assert first.reasoning == second.reasoning

    def test_no_llm_or_network_dependency_exists(self):
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(te))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for banned in ("openai", "anthropic", "requests", "httpx", "urllib", "langchain"):
            assert not any(name.startswith(banned) for name in imported)
