"""CloseLoop Safety Engine: Preflight & Post-Plan Inspection Gates.

Enforces pre-execution safety gates (kill switch, suppression, quiet hours,
call budget, idempotency, consent) and post-plan inspection checkpoints
before any telephony side effect is authorized.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Set
from pydantic import BaseModel, ConfigDict, Field

from closeloop.models import CallPlan, ContactRung, ExecutionLedger, WorkflowSpec
from closeloop.safety import (
    ConsentMissingError,
    E164ValidationError,
    KillSwitchActiveError,
    QuietHoursViolationError,
    SafetyViolationError,
    SuppressionViolationError,
    check_domain_boundaries,
    check_kill_switch,
    check_suppression,
    mask_phone,
    validate_consent_basis,
    validate_e164,
)


class BudgetExhaustedError(SafetyViolationError):
    """Raised when the total call budget policy has been reached or exceeded."""


class IdempotencyCollisionError(SafetyViolationError):
    """Raised when an attempt has already reached a terminal state in the ledger."""


class PlanInspectionError(SafetyViolationError):
    """Raised when a CALL-E CallPlan fails post-planning safety inspection."""


class RecipientMismatchError(SafetyViolationError):
    """Raised when a generated CallPlan targets a phone or rung differing from the intended rung."""


class PreflightCheckResult(BaseModel):
    """Structured report of preflight safety verification."""

    model_config = ConfigDict(extra="allow")

    passed: bool = Field(..., description="True if all preflight safety invariants passed")
    reason: Optional[str] = Field(default=None, description="Explanation if preflight failed")
    checks: dict[str, bool] = Field(default_factory=dict, description="Individual invariant outcomes")
    failed_check: Optional[str] = Field(default=None, description="Name of the first failed check")
    recipient_masked: str = Field(default="", description="Masked phone number of candidate")


class PostPlanInspectionResult(BaseModel):
    """Structured report of post-plan safety inspection."""

    model_config = ConfigDict(extra="allow")

    approved: bool = Field(..., description="True if the plan is authorized for execution")
    reason: Optional[str] = Field(default=None, description="Reason if the plan was rejected")
    domain_violations: list[str] = Field(default_factory=list, description="Prohibited domain patterns detected")
    recipient_verified: bool = Field(default=True, description="True if recipient phone and rung match specification")
    goal_verified: bool = Field(default=True, description="True if goal text contains no prohibited content")


class SafetyEngine:
    """Unified safety enforcement engine for CloseLoop workflows."""

    def __init__(
        self,
        suppression_registry: Optional[Set[str] | list[str]] = None,
        kill_switch_env: str = "CLOSELOOP_KILL_SWITCH",
        kill_switch_file: str | Path = ".closeloop_kill_switch",
    ) -> None:
        self.suppression_registry: set[str] = set(suppression_registry or [])
        self.kill_switch_env = kill_switch_env
        self.kill_switch_file = Path(kill_switch_file)

    def add_to_suppression(self, phone: str) -> None:
        """Add a phone number to the suppression registry (e.g., upon hard refusal)."""
        if phone:
            self.suppression_registry.add(phone.strip())

    def is_suppressed(self, phone: str) -> bool:
        """Check whether a phone number is registered in the suppression list."""
        return check_suppression(phone, self.suppression_registry)

    def preflight_check(
        self,
        workflow: WorkflowSpec,
        rung: ContactRung,
        attempt_num: int,
        ledger: Optional[ExecutionLedger] = None,
        current_dt: Optional[datetime] = None,
    ) -> PreflightCheckResult:
        """Execute full fail-closed preflight safety gating.

        Verifies:
        1. Kill switch inactive
        2. Phone not in suppression registry
        3. Recipient consent basis documented and non-empty
        4. Valid ITU-T E.164 phone formatting
        5. Outside quiet hours in declared timezone
        6. Total call budget not exhausted
        7. No terminal idempotency collision in execution ledger
        """
        masked = mask_phone(rung.phone)
        checks: dict[str, bool] = {
            "kill_switch": False,
            "suppression": False,
            "consent": False,
            "e164": False,
            "quiet_hours": False,
            "budget": False,
            "idempotency": False,
        }

        # 1. Kill switch
        if check_kill_switch(self.kill_switch_env, self.kill_switch_file):
            return PreflightCheckResult(
                passed=False,
                reason="Kill switch is currently engaged via environment variable or control file.",
                checks=checks,
                failed_check="kill_switch",
                recipient_masked=masked,
            )
        checks["kill_switch"] = True

        # 2. Suppression list
        if self.is_suppressed(rung.phone):
            return PreflightCheckResult(
                passed=False,
                reason=f"Recipient number {masked} is present in the suppression list.",
                checks=checks,
                failed_check="suppression",
                recipient_masked=masked,
            )
        checks["suppression"] = True

        # 3. Consent basis
        if not validate_consent_basis(rung.consent_basis):
            return PreflightCheckResult(
                passed=False,
                reason=f"Rung '{rung.rung}' lacks valid, documented consent basis.",
                checks=checks,
                failed_check="consent",
                recipient_masked=masked,
            )
        checks["consent"] = True

        # 4. E.164 phone format
        if not validate_e164(rung.phone):
            return PreflightCheckResult(
                passed=False,
                reason=f"Phone number '{rung.phone}' is not a valid ITU-T E.164 string.",
                checks=checks,
                failed_check="e164",
                recipient_masked=masked,
            )
        checks["e164"] = True

        # 5. Quiet hours
        if workflow.outcome.quiet_hours is not None:
            qh = workflow.outcome.quiet_hours
            if qh.is_active(current_dt=current_dt):
                return PreflightCheckResult(
                    passed=False,
                    reason=f"Target time in timezone '{qh.timezone}' falls within quiet hours ({qh.start} - {qh.end}).",
                    checks=checks,
                    failed_check="quiet_hours",
                    recipient_masked=masked,
                )
        checks["quiet_hours"] = True

        # 6. Call budget
        if ledger is not None:
            if ledger.total_calls_placed >= workflow.policy.max_calls_total:
                return PreflightCheckResult(
                    passed=False,
                    reason=f"Call budget exhausted: {ledger.total_calls_placed} calls placed >= limit {workflow.policy.max_calls_total}.",
                    checks=checks,
                    failed_check="budget",
                    recipient_masked=masked,
                )
        checks["budget"] = True

        # 7. Idempotency collision check
        if ledger is not None:
            if ledger.is_attempt_terminal(rung.rung, attempt_num):
                return PreflightCheckResult(
                    passed=False,
                    reason=f"Attempt {attempt_num} for rung '{rung.rung}' has already completed (terminal idempotency collision).",
                    checks=checks,
                    failed_check="idempotency",
                    recipient_masked=masked,
                )
        checks["idempotency"] = True

        return PreflightCheckResult(
            passed=True,
            reason=None,
            checks=checks,
            failed_check=None,
            recipient_masked=masked,
        )

    def assert_preflight(
        self,
        workflow: WorkflowSpec,
        rung: ContactRung,
        attempt_num: int,
        ledger: Optional[ExecutionLedger] = None,
        current_dt: Optional[datetime] = None,
    ) -> None:
        """Run preflight check and raise the specific SafetyViolationError on any failure."""
        result = self.preflight_check(
            workflow=workflow,
            rung=rung,
            attempt_num=attempt_num,
            ledger=ledger,
            current_dt=current_dt,
        )
        if not result.passed:
            failed = result.failed_check
            if failed == "kill_switch":
                raise KillSwitchActiveError(result.reason)
            elif failed == "suppression":
                raise SuppressionViolationError(result.reason)
            elif failed == "consent":
                raise ConsentMissingError(result.reason)
            elif failed == "e164":
                raise E164ValidationError(result.reason)
            elif failed == "quiet_hours":
                raise QuietHoursViolationError(result.reason)
            elif failed == "budget":
                raise BudgetExhaustedError(result.reason)
            elif failed == "idempotency":
                raise IdempotencyCollisionError(result.reason)
            else:
                raise SafetyViolationError(result.reason or "Preflight safety check failed.")

    def inspect_plan(
        self,
        plan: CallPlan,
        intended_rung: ContactRung,
        workflow: WorkflowSpec,
    ) -> PostPlanInspectionResult:
        """Mandatory post-plan inspection gate (Invariant 4 & 11).

        Verifies that CALL-E's generated plan matches the intended recipient,
        intended ladder rung, and adheres to domain boundaries (no medical,
        financial, legal, or emergency advice).
        """
        # 1. Verify recipient phone and rung
        if plan.phone.strip() != intended_rung.phone.strip():
            reason = f"Plan recipient phone '{plan.phone}' does not match intended rung phone '{intended_rung.phone}'."
            plan.reject(reason)
            return PostPlanInspectionResult(
                approved=False,
                reason=reason,
                domain_violations=[],
                recipient_verified=False,
                goal_verified=True,
            )

        if plan.rung.strip() != intended_rung.rung.strip():
            reason = f"Plan rung identifier '{plan.rung}' does not match intended ladder rung '{intended_rung.rung}'."
            plan.reject(reason)
            return PostPlanInspectionResult(
                approved=False,
                reason=reason,
                domain_violations=[],
                recipient_verified=False,
                goal_verified=True,
            )

        # 2. Check domain boundaries on rendered goal and script
        violations = check_domain_boundaries(plan.goal)
        if plan.script:
            violations.extend(check_domain_boundaries(plan.script))
        # Deduplicate violations while preserving order
        unique_violations = list(dict.fromkeys(violations))

        if unique_violations:
            reason = f"Plan violates prohibited domain boundaries: {', '.join(unique_violations)}."
            plan.reject(reason)
            return PostPlanInspectionResult(
                approved=False,
                reason=reason,
                domain_violations=unique_violations,
                recipient_verified=True,
                goal_verified=False,
            )

        # Inspection passed: approve plan
        plan.approve()
        return PostPlanInspectionResult(
            approved=True,
            reason=None,
            domain_violations=[],
            recipient_verified=True,
            goal_verified=True,
        )

    def assert_plan_inspection(
        self,
        plan: CallPlan,
        intended_rung: ContactRung,
        workflow: WorkflowSpec,
    ) -> None:
        """Run post-plan inspection and raise PlanInspectionError on failure."""
        result = self.inspect_plan(plan, intended_rung, workflow)
        if not result.approved:
            if not result.recipient_verified:
                raise RecipientMismatchError(result.reason)
            raise PlanInspectionError(result.reason or "Post-plan safety inspection rejected the proposed call.")
