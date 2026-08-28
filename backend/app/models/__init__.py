"""ORM model package.

Importing this package registers every mapper on ``Base.metadata``, which is
what makes ``database.init_db()`` able to create the full schema. Import models
from here (``from app.models import RiskEvent``) rather than from the individual
modules, so relationship resolution never depends on import order.
"""

from __future__ import annotations

from app.models.action_lock import ActionLock
from app.models.audit_log import AuditLog, ImmutableAuditLogError
from app.models.communication_log import CommunicationLog
from app.models.customer_profile import CustomerProfile
from app.models.decision import Decision
from app.models.diagnosis import Diagnosis, MLDiagnosisPrediction
from app.models.merchant import Merchant
from app.models.outcome import Outcome
from app.models.recovery_run import RecoveryRun
from app.models.payment_attempt import PaymentAttempt
from app.models.policy import Policy
from app.models.promise_to_pay import PromiseToPay
from app.models.risk_event import RiskEvent
from app.models.stopping_rule_state import StoppingRuleState

__all__ = [
    "ActionLock",
    "AuditLog",
    "CommunicationLog",
    "CustomerProfile",
    "Decision",
    "Diagnosis",
    "ImmutableAuditLogError",
    "MLDiagnosisPrediction",
    "Merchant",
    "Outcome",
    "RecoveryRun",
    "PaymentAttempt",
    "Policy",
    "PromiseToPay",
    "RiskEvent",
    "StoppingRuleState",
]
