"""Diagnosis classifier — loads the trained model and exposes predict().
BUILD_SPEC Section 4a.

This module owns the FEATURE CONTRACT. Training (train_diagnosis_model.py) and
inference (decision_engine.py) both build their feature vectors by calling
:func:`extract_features` here, so the two can never drift apart — a skew between
how a model was trained and how it is called is the classic silent ML failure,
and sharing one function makes it structurally impossible.

Features are exactly the seven Section 4a names, in a fixed order:

    amount, attempt_number, days_since_event, gateway_error_code,
    customer_success_rate, event_type, time_of_day

Non-blocking
------------
Section 4a: "if the classifier fails or is unavailable (model file missing,
exception during predict()), the pipeline logs it and continues on the
deterministic rule-based diagnosis alone — ML is an enhancement layer, never a
dependency the core loop can be broken by."

:func:`predict` therefore returns ``None`` on ANY failure and never propagates
an exception. Callers treat None as "no ML opinion available", which is a normal
state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.enums import EventType, RootCauseCode

logger = logging.getLogger(__name__)

#: Section 4a feature list, in the order the model expects.
FEATURE_NAMES: tuple[str, ...] = (
    "amount",
    "attempt_number",
    "days_since_event",
    "gateway_error_code",
    "customer_success_rate",
    "event_type",
    "time_of_day",
)

#: Default artifact locations. Manifest paths, Section 12.
MODEL_DIR = Path(__file__).parent / "models"
MODEL_PATH = MODEL_DIR / "diagnosis_classifier.joblib"
METRICS_PATH = MODEL_DIR / "metrics.json"

#: Below this, the ML opinion is treated as too weak to count as agreement, and
#: the event is routed to /exceptions for review (Section 4a).
ML_CONFIDENCE_THRESHOLD = 0.60

#: IST, matching the synthetic generator. time_of_day is a local-hour feature:
#: "did this happen at 3am" only means something in the customer's own timezone.
IST = timezone(timedelta(hours=5, minutes=30), "IST")

#: Placeholder for a missing gateway error code. A real category rather than a
#: null, because "no code at all" is itself informative — invoice_overdue events
#: never have one.
NO_ERROR_CODE = "__none__"


@dataclass(frozen=True)
class MLPrediction:
    """One classifier opinion."""

    root_cause: RootCauseCode
    confidence: float
    model_version: str

    @property
    def is_confident(self) -> bool:
        return self.confidence >= ML_CONFIDENCE_THRESHOLD


def extract_features(
    *,
    amount: Any,
    attempt_number: int,
    detected_at: datetime,
    gateway_error_code: str | None,
    customer_success_rate: float,
    event_type: EventType | str,
    now: datetime,
) -> dict[str, Any]:
    """Build the Section 4a feature dict for one event.

    Shared by training and inference so the two cannot diverge.

    ``amount`` is converted to float here. That is safe and deliberate: it feeds
    a decision tree's split comparisons, never an accounting total. Money that
    is stored or reported still goes through ``Decimal`` everywhere else.
    """
    resolved_type = (
        event_type.value if isinstance(event_type, EventType) else str(event_type)
    )
    local_detected = detected_at.astimezone(IST)
    days_since = max(0, (now - detected_at).days)

    return {
        "amount": float(amount) if amount is not None else 0.0,
        "attempt_number": int(attempt_number),
        "days_since_event": days_since,
        "gateway_error_code": gateway_error_code or NO_ERROR_CODE,
        "customer_success_rate": float(customer_success_rate),
        "event_type": resolved_type,
        "time_of_day": local_detected.hour,
    }


def encode_row(features: dict[str, Any], encoders: dict[str, dict[str, int]]) -> list[float]:
    """Turn a feature dict into the numeric vector the tree expects.

    Categorical features use the integer maps saved alongside the model at
    training time. An unseen category maps to -1 rather than raising: a new
    Razorpay error code should degrade the prediction, not break the pipeline.
    """
    row: list[float] = []
    for name in FEATURE_NAMES:
        value = features[name]
        if name in encoders:
            row.append(float(encoders[name].get(str(value), -1)))
        else:
            row.append(float(value))
    return row


class DiagnosisClassifier:
    """Loaded model + its encoders. Construct via :meth:`load`."""

    def __init__(
        self,
        model: Any,
        encoders: dict[str, dict[str, int]],
        classes: list[str],
        model_version: str,
        feature_names: tuple[str, ...] = FEATURE_NAMES,
    ) -> None:
        self.model = model
        self.encoders = encoders
        self.classes = classes
        self.model_version = model_version
        self.feature_names = feature_names

    @classmethod
    def load(cls, path: Path | str = MODEL_PATH) -> "DiagnosisClassifier":
        """Load the trained bundle from disk.

        Raises on failure — callers wanting the non-blocking behaviour should
        use :func:`load_classifier`, which swallows and logs.
        """
        import joblib  # imported lazily so the module imports without sklearn

        bundle = joblib.load(Path(path))
        return cls(
            model=bundle["model"],
            encoders=bundle["encoders"],
            classes=list(bundle["classes"]),
            model_version=bundle["model_version"],
            feature_names=tuple(bundle.get("feature_names", FEATURE_NAMES)),
        )

    def predict(self, features: dict[str, Any]) -> MLPrediction:
        """Predict a root cause, with the model's own confidence.

        Confidence is the predicted class's probability from the tree's leaf
        distribution — a real measured quantity, not a constant.
        """
        row = encode_row(features, self.encoders)
        probabilities = self.model.predict_proba([row])[0]
        best_index = int(max(range(len(probabilities)), key=probabilities.__getitem__))
        label = str(self.model.classes_[best_index])
        return MLPrediction(
            root_cause=RootCauseCode(label),
            confidence=float(probabilities[best_index]),
            model_version=self.model_version,
        )


def load_classifier(path: Path | str = MODEL_PATH) -> DiagnosisClassifier | None:
    """Load the classifier, or return None if it is unavailable.

    Section 4a's non-blocking contract starts here: a missing or corrupt model
    file is logged and yields None, never an exception.
    """
    try:
        return DiagnosisClassifier.load(path)
    except FileNotFoundError:
        logger.warning(
            "ml_model_unavailable: no classifier at %s — "
            "continuing on deterministic diagnosis alone",
            path,
        )
        return None
    except Exception:  # noqa: BLE001 - deliberately broad: ML must never block
        logger.exception(
            "ml_model_load_failed: could not load classifier from %s — "
            "continuing on deterministic diagnosis alone",
            path,
        )
        return None


def predict(
    classifier: DiagnosisClassifier | None, features: dict[str, Any]
) -> MLPrediction | None:
    """Run a prediction, returning None on any failure.

    The broad except is the point, not an oversight: Section 4a requires the
    core loop to survive anything this layer does.
    """
    if classifier is None:
        return None
    try:
        return classifier.predict(features)
    except Exception:  # noqa: BLE001 - deliberately broad: ML must never block
        logger.exception(
            "ml_prediction_failed: continuing on deterministic diagnosis alone"
        )
        return None


def load_metrics(path: Path | str = METRICS_PATH) -> dict[str, Any] | None:
    """Read the held-out metrics written by the last training run.

    Backs ``GET /ml/metrics`` (Section 10), which is wired up in a later session.
    """
    import json

    try:
        with open(Path(path), encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        logger.warning("ml_metrics_unavailable: no metrics at %s", path)
        return None
    except Exception:  # noqa: BLE001
        logger.exception("ml_metrics_load_failed: could not read %s", path)
        return None
