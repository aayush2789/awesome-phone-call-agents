"""CloseLoop Orchestration Engine.

Implements the explicit state machine, cascade strategy, outcome classification,
declarative policy routing, and SQLite-backed persistent idempotency.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional
import jsonschema

from closeloop.adapter import CalleAdapterBase, FakeAdapter, PlanRequest
from closeloop.classifier import OutcomeClassifier
from closeloop.ledger import LedgerRepositoryBase, SQLiteLedger
from closeloop.models import (
    AuditEntry,
    CallAttempt,
    Evidence,
    ExecutionLedger,
    OutcomeResult,
    WorkflowResult,
    WorkflowSpec,
)
from closeloop.routing import RoutingAction, RoutingDecision, RoutingEngine
from closeloop.safety_engine import SafetyEngine
from closeloop.validator import WorkflowValidator


class WorkflowState(str, Enum):
    """Explicit lifecycle states for CloseLoop orchestration."""

    INIT = "INIT"
    PREFLIGHT = "PREFLIGHT"
    READY = "READY"
    PLANNING = "PLANNING"
    PLAN_APPROVED = "PLAN_APPROVED"
    RUNNING = "RUNNING"
    RESULT_RECEIVED = "RESULT_RECEIVED"
    RESULT_VALIDATED = "RESULT_VALIDATED"
    CLOSED = "CLOSED"
    RETRY = "RETRY"
    NEXT_RUNG = "NEXT_RUNG"
    SCHEDULE = "SCHEDULE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    BLOCKED = "BLOCKED"
    TERMINATED = "TERMINATED"


def evaluate_stop_condition(condition: Any, structured_result: dict[str, Any]) -> bool:
    """Evaluate whether a structured result satisfies the workflow stop rule."""
    if not condition or not structured_result:
        return False

    if isinstance(condition, str):
        cond_clean = condition.strip()
        # Parse syntax: "decision in [confirmed, declined]" or "decision in ['confirmed', 'declined']"
        if " in " in cond_clean:
            field_part, list_part = cond_clean.split(" in ", 1)
            field = field_part.strip()
            # Extract items inside square brackets
            list_clean = list_part.strip().strip("[]()")
            allowed_items = {item.strip().strip("'\"") for item in list_clean.split(",") if item.strip()}
            actual_val = str(structured_result.get(field, "")).strip().strip("'\"")
            return actual_val in allowed_items
        elif "==" in cond_clean:
            field_part, val_part = cond_clean.split("==", 1)
            field = field_part.strip()
            target_val = val_part.strip().strip("'\"")
            actual_val = str(structured_result.get(field, "")).strip().strip("'\"")
            return actual_val == target_val
        elif "!=" in cond_clean:
            field_part, val_part = cond_clean.split("!=", 1)
            field = field_part.strip()
            target_val = val_part.strip().strip("'\"")
            actual_val = str(structured_result.get(field, "")).strip().strip("'\"")
            return actual_val != target_val
        else:
            # Fallback: check if condition is a field name whose value is truthy
            return bool(structured_result.get(cond_clean))

    elif isinstance(condition, dict):
        # Dict rule syntax: {"field": "decision", "in": ["confirmed", "declined"]}
        field = condition.get("field", "decision")
        actual_val = structured_result.get(field)
        if "in" in condition:
            return actual_val in condition["in"]
        if "equals" in condition:
            return actual_val == condition["equals"]
        if "expression" in condition:
            return evaluate_stop_condition(condition["expression"], structured_result)

    return False


class OrchestrationEngine:
    """CloseLoop Orchestration Engine executing declarative phone-call workflows."""

    def __init__(
        self,
        adapter: Optional[CalleAdapterBase] = None,
        safety_engine: Optional[SafetyEngine] = None,
        validator: Optional[WorkflowValidator] = None,
        classifier: Optional[OutcomeClassifier] = None,
        routing_engine: Optional[RoutingEngine] = None,
        ledger: Optional[LedgerRepositoryBase] = None,
    ) -> None:
        self.adapter: CalleAdapterBase = adapter or FakeAdapter()
        self.safety_engine: SafetyEngine = safety_engine or SafetyEngine()
        self.validator: WorkflowValidator = validator or WorkflowValidator()
        self.classifier: OutcomeClassifier = classifier or OutcomeClassifier()
        self.routing_engine: RoutingEngine = routing_engine or RoutingEngine()
        self.ledger: LedgerRepositoryBase = ledger or SQLiteLedger(":memory:")
        self.state: WorkflowState = WorkflowState.INIT

    def run(
        self,
        workflow_input: WorkflowSpec | str | dict[str, Any] | Path,
        current_dt: Optional[datetime] = None,
    ) -> WorkflowResult:
        """Execute a CloseLoop workflow using Cascade strategy, SQLite ledger, and classification."""
        self.state = WorkflowState.INIT
        now_utc = current_dt or datetime.now(timezone.utc)
        start_time = now_utc

        # 1. Validate Workflow Specification
        validation_report = self.validator.validate(workflow_input)
        if not validation_report.ok:
            self.state = WorkflowState.BLOCKED
            errors_str = "; ".join(f"{e.path}: {e.message}" for e in validation_report.errors)
            run_id = "unknown_run"
            if isinstance(workflow_input, WorkflowSpec):
                run_id = workflow_input.run_id
            elif isinstance(workflow_input, dict):
                run_id = workflow_input.get("run_id", "unknown_run")

            res = WorkflowResult(
                run_id=run_id,
                status="blocked",
                outcome="error",
                summary=f"Workflow failed pre-execution specification validation: {errors_str}",
                started_at=start_time,
                completed_at=datetime.now(timezone.utc),
            )
            return res

        # Parse valid WorkflowSpec
        if isinstance(workflow_input, WorkflowSpec):
            workflow = workflow_input
        elif isinstance(workflow_input, Path):
            workflow = WorkflowSpec.from_yaml_file(str(workflow_input))
        elif isinstance(workflow_input, str):
            workflow = WorkflowSpec.from_yaml(workflow_input)
        else:
            workflow = WorkflowSpec(**workflow_input)

        # Register workflow in persistent ledger
        self.ledger.record_workflow(workflow)

        # Crash Recovery: Reconcile stranded in-flight attempts from prior runs
        stranded = self.ledger.reconcile_in_flight_attempts(workflow.run_id)
        if stranded:
            audit_recon = AuditEntry(
                rung="system",
                attempt=0,
                action="crash_recovery_reconciliation",
                details={"stranded_attempts_reconciled": len(stranded)},
            )
            self.ledger.record_audit(workflow.run_id, audit_recon)

        # Synchronize execution tracking
        in_memory_ledger = ExecutionLedger(run_id=workflow.run_id)
        existing_attempts = self.ledger.list_attempts(workflow.run_id)
        for att in existing_attempts:
            in_memory_ledger.record_attempt(att)
            if att.status == "terminal":
                in_memory_ledger.total_calls_placed += 1

        total_ladder_attempts = sum(r.max_attempts for r in workflow.ladder)
        max_possible_calls = min(total_ladder_attempts, workflow.policy.max_calls_total)

        final_result: Optional[WorkflowResult] = None

        # Cascade Strategy Loop: Iterate sequentially through rungs
        for rung_idx, rung in enumerate(workflow.ladder):
            rung_attempt_count = 0
            next_rung_obj = workflow.ladder[rung_idx + 1] if rung_idx + 1 < len(workflow.ladder) else None

            while rung_attempt_count < rung.max_attempts:
                # Check global call budget
                if in_memory_ledger.total_calls_placed >= workflow.policy.max_calls_total:
                    self.state = WorkflowState.TERMINATED
                    audit_entry = AuditEntry(
                        rung=rung.rung,
                        attempt=rung_attempt_count + 1,
                        action="budget_exhausted",
                        details={"total_placed": in_memory_ledger.total_calls_placed, "limit": workflow.policy.max_calls_total},
                    )
                    self.ledger.record_audit(workflow.run_id, audit_entry)
                    break

                attempt_index = rung_attempt_count + 1

                # Check Persistent Ledger for existing terminal attempt (Idempotency)
                if self.ledger.is_attempt_terminal(workflow.run_id, rung.rung, attempt_index):
                    rung_attempt_count += 1
                    continue

                # Step 2: PREFLIGHT Safety Gate
                self.state = WorkflowState.PREFLIGHT
                preflight = self.safety_engine.preflight_check(
                    workflow=workflow,
                    rung=rung,
                    attempt_num=attempt_index,
                    ledger=in_memory_ledger,
                    current_dt=now_utc,
                )

                if not preflight.passed:
                    self.state = WorkflowState.BLOCKED
                    audit_entry = AuditEntry(
                        rung=rung.rung,
                        attempt=attempt_index,
                        action="preflight_failed",
                        details={"reason": preflight.reason, "failed_check": preflight.failed_check},
                    )
                    self.ledger.record_audit(workflow.run_id, audit_entry)

                    if preflight.failed_check in {"kill_switch", "suppression"}:
                        final_result = WorkflowResult(
                            run_id=workflow.run_id,
                            status="blocked",
                            outcome="blocked",
                            summary=f"Workflow blocked by safety gate: {preflight.reason}",
                            calls_placed=in_memory_ledger.total_calls_placed,
                            calls_avoided=max(0, max_possible_calls - in_memory_ledger.total_calls_placed),
                            started_at=start_time,
                            completed_at=datetime.now(timezone.utc),
                            audit=self.ledger.get_audit_trail(workflow.run_id),
                        )
                        self.ledger.save_workflow_result(final_result)
                        self._writeback_if_configured(workflow, final_result)
                        return final_result
                    else:
                        # Rung skipped due to quiet hours or non-fatal preflight issue, advance to next rung
                        break

                # Step 3: READY -> PLANNING
                self.state = WorkflowState.READY
                self.state = WorkflowState.PLANNING

                goal_rendered = (
                    f"Engage recipient regarding outcome '{workflow.outcome.name}'. "
                    f"Rung: {rung.rung}. Identify decision and required parameters."
                )
                plan_request = PlanRequest(
                    run_id=workflow.run_id,
                    rung=rung.rung,
                    phone=rung.phone,
                    goal=goal_rendered,
                    language=rung.language,
                )
                call_plan = self.adapter.plan(plan_request)

                # Step 4: Mandatory Post-Plan Inspection Gate
                inspection = self.safety_engine.inspect_plan(call_plan, rung, workflow)
                if not inspection.approved:
                    self.state = WorkflowState.BLOCKED
                    audit_entry = AuditEntry(
                        rung=rung.rung,
                        attempt=attempt_index,
                        action="plan_rejected",
                        details={"reason": inspection.reason, "violations": inspection.domain_violations},
                    )
                    self.ledger.record_audit(workflow.run_id, audit_entry)

                    final_result = WorkflowResult(
                        run_id=workflow.run_id,
                        status="blocked",
                        outcome="error",
                        summary=f"CallPlan rejected by post-plan safety inspection: {inspection.reason}",
                        calls_placed=in_memory_ledger.total_calls_placed,
                        calls_avoided=max(0, max_possible_calls - in_memory_ledger.total_calls_placed),
                        started_at=start_time,
                        completed_at=datetime.now(timezone.utc),
                        audit=self.ledger.get_audit_trail(workflow.run_id),
                    )
                    self.ledger.save_workflow_result(final_result)
                    self._writeback_if_configured(workflow, final_result)
                    return final_result

                self.state = WorkflowState.PLAN_APPROVED

                # Step 5: RUNNING (Transactional State Transition planned -> executing)
                self.state = WorkflowState.RUNNING
                attempt = CallAttempt(
                    run_id=workflow.run_id,
                    rung=rung.rung,
                    attempt=attempt_index,
                    phone=rung.phone,
                    status="planned",
                    plan=call_plan,
                )
                # Persist planned attempt in SQLite
                if not self.ledger.has_attempt(workflow.run_id, rung.rung, attempt_index):
                    self.ledger.record_attempt(attempt)

                # Transition to executing before side effect
                self.ledger.update_attempt_status(attempt.idempotency_key, "executing")
                attempt.status = "executing"

                call_run = self.adapter.run(call_plan)
                attempt.run = call_run
                in_memory_ledger.total_calls_placed += 1
                rung_attempt_count += 1

                audit_started = AuditEntry(
                    rung=rung.rung,
                    attempt=attempt_index,
                    action="call_started",
                    details={"external_call_id": call_run.external_call_id, "phone": rung.phone},
                )
                self.ledger.record_audit(workflow.run_id, audit_started)

                # Step 6: RESULT_RECEIVED (Bounded Polling)
                self.state = WorkflowState.RESULT_RECEIVED
                status_res = self.adapter.status(call_run.run_id)
                poll_max = 5
                polls = 0
                while status_res.status == "running" and polls < poll_max:
                    polls += 1
                    status_res = self.adapter.status(call_run.run_id)

                call_run.status = status_res.status
                call_run.structured_result = status_res.structured_result
                call_run.error = status_res.error

                # Step 7: RESULT_VALIDATED via Deterministic OutcomeClassifier
                self.state = WorkflowState.RESULT_VALIDATED
                outcome_result = self.classifier.classify(status_res, workflow.outcome)

                # Atomically transition attempt to terminal in persistent ledger
                attempt.outcome = outcome_result
                attempt.status = "terminal"
                self.ledger.update_attempt_status(
                    attempt.idempotency_key,
                    "terminal",
                    run=call_run,
                    outcome=outcome_result,
                )
                in_memory_ledger.record_attempt(attempt)

                audit_validated = AuditEntry(
                    rung=rung.rung,
                    attempt=attempt_index,
                    action="result_validated",
                    outcome_class=outcome_result.outcome_class,
                    details={
                        "stop_condition_met": outcome_result.stop_condition_met,
                        "schema_valid": outcome_result.result_validation.get("valid", True),
                        "decision": outcome_result.decision,
                        "confidence": outcome_result.confidence,
                    },
                )
                self.ledger.record_audit(workflow.run_id, audit_validated)

                # Step 8: Declarative Routing via RoutingEngine
                routing_decision = self.routing_engine.route(
                    outcome=outcome_result,
                    current_rung=rung,
                    policy=workflow.policy,
                    attempts_on_rung=rung_attempt_count,
                    total_calls_placed=in_memory_ledger.total_calls_placed,
                    next_rung=next_rung_obj,
                )

                if routing_decision.action == RoutingAction.CLOSE:
                    self.state = WorkflowState.CLOSED
                    calls_avoided = max(0, max_possible_calls - in_memory_ledger.total_calls_placed)

                    audit_closed = AuditEntry(
                        rung=rung.rung,
                        attempt=attempt_index,
                        action="closed",
                        outcome_class=outcome_result.outcome_class,
                        details={"closed_on_rung": rung.rung, "calls_avoided": calls_avoided},
                    )
                    self.ledger.record_audit(workflow.run_id, audit_closed)

                    final_result = WorkflowResult(
                        run_id=workflow.run_id,
                        status="closed",
                        outcome=outcome_result.outcome_class,
                        summary=f"Outcome '{workflow.outcome.name}' successfully closed on rung '{rung.rung}'.",
                        structured_result=outcome_result.structured_result,
                        result_validation=outcome_result.result_validation,
                        closed_on_rung=rung.rung,
                        attempt_index=attempt_index,
                        calls_placed=in_memory_ledger.total_calls_placed,
                        calls_avoided=calls_avoided,
                        external_call_id=call_run.external_call_id,
                        recipient_phone_e164=rung.phone,
                        started_at=start_time,
                        completed_at=datetime.now(timezone.utc),
                        audit=self.ledger.get_audit_trail(workflow.run_id),
                    )
                    self.ledger.save_workflow_result(final_result)
                    self._writeback_if_configured(workflow, final_result)
                    return final_result

                elif routing_decision.action == RoutingAction.SUPPRESS_AND_STOP:
                    self.state = WorkflowState.TERMINATED
                    if routing_decision.suppress_phone:
                        self.safety_engine.add_to_suppression(routing_decision.suppress_phone)

                    calls_avoided = max(0, max_possible_calls - in_memory_ledger.total_calls_placed)
                    audit_supp = AuditEntry(
                        rung=rung.rung,
                        attempt=attempt_index,
                        action="suppressed_and_terminated",
                        outcome_class="hard_refusal",
                        details={"policy": "stop_chain", "reason": routing_decision.reason},
                    )
                    self.ledger.record_audit(workflow.run_id, audit_supp)

                    final_result = WorkflowResult(
                        run_id=workflow.run_id,
                        status="terminated",
                        outcome="hard_refusal",
                        summary=f"Workflow permanently stopped due to explicit refusal. Phone {rung.phone} added to suppression registry.",
                        structured_result=outcome_result.structured_result,
                        result_validation=outcome_result.result_validation,
                        closed_on_rung=rung.rung,
                        attempt_index=attempt_index,
                        calls_placed=in_memory_ledger.total_calls_placed,
                        calls_avoided=calls_avoided,
                        external_call_id=call_run.external_call_id,
                        recipient_phone_e164=rung.phone,
                        started_at=start_time,
                        completed_at=datetime.now(timezone.utc),
                        audit=self.ledger.get_audit_trail(workflow.run_id),
                    )
                    self.ledger.save_workflow_result(final_result)
                    self._writeback_if_configured(workflow, final_result)
                    return final_result

                elif routing_decision.action == RoutingAction.NEXT_RUNG:
                    self.state = WorkflowState.NEXT_RUNG
                    break  # Break attempt loop on current rung, advance to next rung in ladder

                elif routing_decision.action == RoutingAction.RETRY:
                    self.state = WorkflowState.RETRY
                    continue

                elif routing_decision.action == RoutingAction.SCHEDULE:
                    self.state = WorkflowState.SCHEDULE
                    # For synchronous execution, advance or break according to policy
                    break

                elif routing_decision.action in {RoutingAction.FAIL_CLOSED, RoutingAction.TERMINATE}:
                    self.state = WorkflowState.TERMINATED
                    break

            if self.state == WorkflowState.TERMINATED:
                break

        # Terminal Fallback: All rungs or budgets exhausted without closure
        self.state = WorkflowState.TERMINATED
        final_result = WorkflowResult(
            run_id=workflow.run_id,
            status="not_closed",
            outcome="unresolved",
            summary=f"Outcome '{workflow.outcome.name}' could not be closed. All rungs/budgets exhausted.",
            structured_result=None,
            result_validation=None,
            closed_on_rung=None,
            attempt_index=None,
            calls_placed=in_memory_ledger.total_calls_placed,
            calls_avoided=0,
            external_call_id=None,
            recipient_phone_e164=None,
            started_at=start_time,
            completed_at=datetime.now(timezone.utc),
            audit=self.ledger.get_audit_trail(workflow.run_id),
        )
        self.ledger.save_workflow_result(final_result)
        self._writeback_if_configured(workflow, final_result)
        return final_result

    def _writeback_if_configured(self, workflow: WorkflowSpec, result: WorkflowResult) -> None:
        """Export outcome result if writeback is configured."""
        if not workflow.writeback:
            return

        wb = workflow.writeback
        if not wb.path:
            return

        try:
            target_path = Path(wb.path)
            target_path.parent.mkdir(parents=True, exist_ok=True)

            if wb.target == "json":
                with open(target_path, "w", encoding="utf-8") as f:
                    json.dump(result.to_envelope_dict(), f, indent=2)

            elif wb.target == "csv":
                file_exists = target_path.is_file()
                envelope = result.to_envelope_dict()
                row = {
                    "run_id": envelope["run_id"],
                    "status": envelope["status"],
                    "outcome": envelope["outcome"],
                    "closed_on_rung": envelope.get("closed_on_rung") or "",
                    "calls_placed": envelope["calls_placed"],
                    "calls_avoided": envelope["calls_avoided"],
                    "recipient_phone": envelope.get("recipient_phone_e164") or "",
                    "completed_at": envelope.get("completed_at") or "",
                }
                with open(target_path, "a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                    if not file_exists:
                        writer.writeheader()
                    writer.writerow(row)
        except Exception:
            pass


def execute_workflow(
    workflow: WorkflowSpec | str | dict[str, Any] | Path,
    adapter: Optional[CalleAdapterBase] = None,
    current_dt: Optional[datetime] = None,
    ledger: Optional[LedgerRepositoryBase] = None,
) -> WorkflowResult:
    """Convenience function to run a CloseLoop workflow with default orchestration."""
    engine = OrchestrationEngine(adapter=adapter, ledger=ledger)
    return engine.run(workflow, current_dt=current_dt)
