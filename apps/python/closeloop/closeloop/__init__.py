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

from closeloop.validator import (
    ValidationErrorDetail,
    ValidationReport,
    WorkflowValidator,
    validate_workflow_spec,
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
    "ValidationErrorDetail",
    "ValidationReport",
    "WorkflowResult",
    "WorkflowSpec",
    "WorkflowValidator",
    "WritebackConfig",
    "compute_idempotency_key",
    "validate_workflow_spec",
]
