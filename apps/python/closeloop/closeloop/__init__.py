"""CloseLoop: Outcome-driven phone-call orchestration runtime built on top of CALL-E."""

from closeloop.models import (
    AuditEntry,
    CallAttempt,
    CallPlan,
    CallRun,
    ContactRung,
    Evidence,
    ExecutionLedger,
    OutcomeContract,
    OutcomeResult,
    Policy,
    QuietHoursConfig,
    StrategyConfig,
    WorkflowResult,
    WorkflowSpec,
    WritebackConfig,
    compute_idempotency_key,
)

__version__ = "0.1.0"

__all__ = [
    "AuditEntry",
    "CallAttempt",
    "CallPlan",
    "CallRun",
    "ContactRung",
    "Evidence",
    "ExecutionLedger",
    "OutcomeContract",
    "OutcomeResult",
    "Policy",
    "QuietHoursConfig",
    "StrategyConfig",
    "WorkflowResult",
    "WorkflowSpec",
    "WritebackConfig",
    "compute_idempotency_key",
]
