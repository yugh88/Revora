"""Structured JSON logging + correlation IDs. BUILD_SPEC Sections 1 and 12.

Run from the backend/ directory:

    cd backend && PYTHONPATH=. pytest -q

Three properties are worth real tests here.

Logs must be MACHINE-READABLE. Every line is parsed with ``json.loads`` rather
than searched for substrings — a test that greps for a field name would pass
against output that is not valid JSON at all.

Correlation IDs must PROPAGATE. The id lives in a ContextVar so call sites never
pass it explicitly, which is convenient and also means a broken binding would be
invisible: the code would look right and the field would silently vanish. So the
tests assert the id appears on lines emitted by code that never mentions it.

Secrets must NOT LEAK. Redaction is tested from both directions — by field name
and by value shape — because each catches a different accident.
"""

from __future__ import annotations

import io
import json
import logging
import threading

import pytest

from app.services.logging_config import (
    REDACTED,
    JSONFormatter,
    configure_logging,
    correlation_scope,
    get_correlation_id,
    log_event,
    new_correlation_id,
    redact,
    set_correlation_id,
)


@pytest.fixture()
def capture():
    """Capture log output as parsed JSON objects."""
    stream = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=stream)
    logger = logging.getLogger("revora.test")

    def lines() -> list[dict]:
        stream.seek(0)
        return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]

    yield logger, lines
    logging.getLogger().handlers.clear()
    logging.disable(logging.NOTSET)


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #


class TestLogsAreStructuredJSON:
    def test_every_line_parses_as_json(self, capture):
        logger, lines = capture
        logger.info("first")
        logger.warning("second")
        logger.error("third")
        assert len(lines()) == 3

    def test_the_required_fields_are_present(self, capture):
        """Section 12: structured logs need at least these."""
        logger, lines = capture
        log_event(
            logger,
            logging.INFO,
            "record_processed",
            event_id="evt_1",
            stage="decision",
            action="update_card_email",
            outcome="recovered",
            correlation_id="corr_1",
        )
        entry = lines()[0]
        for field in (
            "timestamp",
            "level",
            "logger",
            "message",
            "event_id",
            "correlation_id",
            "stage",
            "action",
            "outcome",
        ):
            assert field in entry, f"missing {field}"

    def test_the_timestamp_is_iso_with_a_timezone(self, capture):
        from datetime import datetime

        logger, lines = capture
        logger.info("x")
        parsed = datetime.fromisoformat(lines()[0]["timestamp"])
        assert parsed.tzinfo is not None

    def test_the_level_is_recorded(self, capture):
        logger, lines = capture
        logger.warning("careful")
        assert lines()[0]["level"] == "WARNING"

    def test_extra_context_becomes_structured_fields(self, capture):
        """Context belongs in its own keys, not interpolated into a message
        string that would then have to be parsed back out."""
        logger, lines = capture
        log_event(
            logger,
            logging.INFO,
            "batch_finished",
            stage="batch",
            action="finish",
            processed=42,
            recovery_rate=0.1293,
        )
        entry = lines()[0]
        assert entry["processed"] == 42
        assert entry["recovery_rate"] == 0.1293

    def test_nothing_is_printed_instead_of_logged(self, capture):
        """A print statement would not appear in the handler's stream at all."""
        logger, lines = capture
        logger.info("only via logging")
        assert lines()[0]["message"] == "only via logging"

    def test_non_serialisable_values_do_not_break_a_line(self, capture):
        """Logging must never be the thing that takes the process down."""
        from decimal import Decimal

        logger, lines = capture
        log_event(
            logger,
            logging.INFO,
            "money",
            amount=Decimal("2499.00"),
            obj=object(),
        )
        entry = lines()[0]
        assert entry["amount"] == "2499.00"

    def test_reconfiguring_does_not_duplicate_lines(self, capture):
        logger, _ = capture
        stream = io.StringIO()
        configure_logging(stream=stream)
        logging.getLogger("revora.test").info("once")
        assert len(stream.getvalue().strip().splitlines()) == 1


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class TestErrorLogging:
    def test_an_exception_is_logged_with_structured_error_detail(self, capture):
        logger, lines = capture
        try:
            raise ValueError("something specific went wrong")
        except ValueError:
            log_event(
                logger,
                logging.ERROR,
                "record_isolated_failure",
                stage="execution",
                action="isolate_failure",
                outcome="failed",
                exc_info=True,
            )
        entry = lines()[0]
        assert entry["level"] == "ERROR"
        assert entry["error"]["type"] == "ValueError"
        assert "something specific" in entry["error"]["message"]

    def test_the_traceback_is_preserved(self, capture):
        """Section 9 requires failures to be caught AND logged; a failure with
        no traceback is not much use when diagnosing one."""
        logger, lines = capture
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            log_event(logger, logging.ERROR, "failed", exc_info=True)
        traceback_lines = lines()[0]["error"]["traceback"]
        assert isinstance(traceback_lines, list)
        assert any("RuntimeError" in line for line in traceback_lines)

    def test_the_traceback_stays_inside_the_json_object(self, capture):
        """A raw multi-line traceback would break one-object-per-line parsing."""
        logger, lines = capture
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            log_event(logger, logging.ERROR, "failed", exc_info=True)
        assert len(lines()) == 1


# --------------------------------------------------------------------------- #
# Correlation IDs
# --------------------------------------------------------------------------- #


class TestCorrelationIds:
    def test_a_generated_id_is_unique(self):
        ids = {new_correlation_id() for _ in range(200)}
        assert len(ids) == 200

    def test_a_scope_binds_and_restores(self):
        assert get_correlation_id() is None
        with correlation_scope("corr_outer"):
            assert get_correlation_id() == "corr_outer"
        assert get_correlation_id() is None

    def test_scopes_nest_correctly(self):
        with correlation_scope("outer"):
            with correlation_scope("inner"):
                assert get_correlation_id() == "inner"
            assert get_correlation_id() == "outer"

    def test_a_scope_restores_even_when_the_body_raises(self):
        """A batch record that fails must not leak its id onto the next one."""
        with correlation_scope("first"):
            with pytest.raises(RuntimeError):
                with correlation_scope("second"):
                    raise RuntimeError("record failed")
            assert get_correlation_id() == "first"

    def test_the_id_is_attached_without_being_passed(self, capture):
        """The ContextVar binding is the whole mechanism; if it broke, call
        sites would look correct and the field would silently disappear."""
        logger, lines = capture
        with correlation_scope("corr_ambient"):
            logger.info("no correlation id mentioned here")
        assert lines()[0]["correlation_id"] == "corr_ambient"

    def test_no_id_outside_a_scope(self, capture):
        logger, lines = capture
        set_correlation_id(None)
        logger.info("unscoped")
        assert "correlation_id" not in lines()[0]

    def test_an_explicit_id_overrides_the_ambient_one(self, capture):
        logger, lines = capture
        with correlation_scope("ambient"):
            log_event(logger, logging.INFO, "explicit", correlation_id="override")
        assert lines()[0]["correlation_id"] == "override"

    def test_ids_do_not_bleed_across_threads(self, capture):
        """A ContextVar, not a global. Under concurrent requests a global would
        cross-contaminate every log line."""
        logger, lines = capture
        seen: dict[str, str | None] = {}

        def worker(name: str) -> None:
            with correlation_scope(f"corr_{name}"):
                seen[name] = get_correlation_id()

        threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(6)]
        with correlation_scope("corr_main"):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert get_correlation_id() == "corr_main"
        assert seen == {str(i): f"corr_{i}" for i in range(6)}


class TestCorrelationIdMiddleware:
    """The HTTP end of the chain. Section 12: "correlation-id middleware"."""

    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_a_response_carries_a_correlation_id(self):
        response = self._client().get("/health")
        assert response.headers.get("X-Correlation-ID")

    def test_an_inbound_id_is_honoured(self):
        """So a caller can tie the request to their own tracing."""
        response = self._client().get(
            "/health", headers={"X-Correlation-ID": "corr_from_caller"}
        )
        assert response.headers["X-Correlation-ID"] == "corr_from_caller"

    def test_each_request_gets_its_own_generated_id(self):
        client = self._client()
        first = client.get("/health").headers["X-Correlation-ID"]
        second = client.get("/health").headers["X-Correlation-ID"]
        assert first != second


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #


class TestSecretsAreNeverLogged:
    def test_a_secret_field_name_is_redacted(self, capture):
        logger, lines = capture
        log_event(
            logger,
            logging.INFO,
            "config",
            razorpay_key_secret="super_secret_value",
            api_key="abcd1234",
            password="hunter2",
        )
        entry = lines()[0]
        assert entry["razorpay_key_secret"] == REDACTED
        assert entry["api_key"] == REDACTED
        assert entry["password"] == REDACTED
        assert "super_secret_value" not in json.dumps(entry)

    def test_a_razorpay_key_is_redacted_wherever_it_appears(self, capture):
        """Field-name redaction alone would miss a key embedded in a message or
        arriving under an innocuous key."""
        logger, lines = capture
        log_event(
            logger,
            logging.INFO,
            "gateway call failed for rzp_test_AbCdEf123456",
            note="using rzp_test_AbCdEf123456",
        )
        blob = json.dumps(lines()[0])
        assert "rzp_test_AbCdEf123456" not in blob
        assert REDACTED in blob

    def test_a_nested_secret_is_redacted(self, capture):
        logger, lines = capture
        log_event(
            logger,
            logging.INFO,
            "settings",
            config={"database_url": "sqlite:///x.db", "razorpay_key_id": "rzp_test_XYZ"},
        )
        entry = lines()[0]
        assert entry["config"]["razorpay_key_id"] == REDACTED
        assert entry["config"]["database_url"] == "sqlite:///x.db"

    def test_a_secret_inside_a_list_is_redacted(self, capture):
        logger, lines = capture
        log_event(logger, logging.INFO, "keys", items=[{"token": "abc"}, {"safe": "ok"}])
        entry = lines()[0]
        assert entry["items"][0]["token"] == REDACTED
        assert entry["items"][1]["safe"] == "ok"

    def test_a_secret_in_an_exception_message_is_redacted(self, capture):
        """Exception text is a classic leak path."""
        logger, lines = capture
        try:
            raise ValueError("auth failed with key rzp_test_LEAKED9999")
        except ValueError:
            log_event(logger, logging.ERROR, "gateway_error", exc_info=True)
        blob = json.dumps(lines()[0])
        assert "rzp_test_LEAKED9999" not in blob

    def test_non_secret_fields_survive(self, capture):
        """Over-redacting would make the logs useless."""
        logger, lines = capture
        log_event(
            logger,
            logging.INFO,
            "record",
            event_id="evt_1",
            amount="2499.00",
            action="sms_reminder",
        )
        entry = lines()[0]
        assert entry["event_id"] == "evt_1"
        assert entry["amount"] == "2499.00"
        assert entry["action"] == "sms_reminder"

    def test_redact_handles_deep_structures_without_hanging(self):
        """Depth is bounded so a pathological structure cannot wedge the logger."""
        deep: dict = {}
        node = deep
        for _ in range(50):
            node["next"] = {}
            node = node["next"]
        assert redact(deep) is not None

    def test_the_formatter_is_installed_on_the_root_logger(self, capture):
        assert isinstance(logging.getLogger().handlers[0].formatter, JSONFormatter)


class TestApplicationStartupLogsNoSecrets:
    def test_startup_reports_only_whether_keys_exist(self):
        """main.py logs razorpay_configured (a bool), never the keys."""
        import inspect

        from app import main

        source = inspect.getsource(main)
        assert "razorpay_configured" in source
        assert "razorpay_key_secret" not in source
        assert "settings.razorpay_key_id" not in source
