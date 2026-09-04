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

from closeloop.adapter import (
    ALL_OUTCOME_CLASSES,
    AuthStatus,
    CallStatus,
    CalleAdapterBase,
    FakeAdapter,
    PlanInspectionResult,
    PlanRequest,
    ToolsStatus,
)

from closeloop.safety_engine import (
    BudgetExhaustedError,
    IdempotencyCollisionError,
    PlanInspectionError,
    PostPlanInspectionResult,
    PreflightCheckResult,
    RecipientMismatchError,
    SafetyEngine,
)

__version__ = "0.1.0"

__all__ = [
    "ALL_OUTCOME_CLASSES",
    "AuditEntry",
    "AuthStatus",
    "BudgetExhaustedError",
    "CallAttempt",
    "CallPlan",
    "CallRun",
    "CallStatus",
    "CalleAdapterBase",
    "ContactRung",
    "Evidence",
    "ExecutionLedger",
    "FakeAdapter",
    "IdempotencyCollisionError",
    "OutcomeContract",
    "OutcomeResult",
    "PlanInspectionError",
    "PlanInspectionResult",
    "PlanRequest",
    "Policy",
    "PostPlanInspectionResult",
    "PreflightCheckResult",
    "QuietHoursConfig",
    "RecipientMismatchError",
    "SafetyEngine",
    "StrategyConfig",
    "ToolsStatus",
    "ValidationErrorDetail",
    "ValidationReport",
    "WorkflowResult",
    "WorkflowSpec",
    "WorkflowValidator",
    "WritebackConfig",
    "compute_idempotency_key",
    "validate_workflow_spec",
]
