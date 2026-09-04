"""CloseLoop Orchestration Engine.

Implements the explicit state machine and cascade orchestration strategy.
Executes workflows across rungs, enforces preflight and post-plan inspection
gates, manages bounded polling, evaluates outcome contracts, and computes
calls placed vs avoided.
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
from closeloop.models import (
    AuditEntry,
    CallAttempt,
    Evidence,
    ExecutionLedger,
    OutcomeResult,
    WorkflowResult,
    WorkflowSpec,
)
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
    ) -> None:
        self.adapter: CalleAdapterBase = adapter or FakeAdapter()
        self.safety_engine: SafetyEngine = safety_engine or SafetyEngine()
        self.validator: WorkflowValidator = validator or WorkflowValidator()
        self.state: WorkflowState = WorkflowState.INIT

    def run(
        self,
        workflow_input: WorkflowSpec | str | dict[str, Any] | Path,
        current_dt: Optional[datetime] = None,
    ) -> WorkflowResult:
        """Execute a CloseLoop workflow using the Cascade strategy."""
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

            return WorkflowResult(
                run_id=run_id,
                status="blocked",
                outcome="error",
                summary=f"Workflow failed pre-execution specification validation: {errors_str}",
                started_at=start_time,
                completed_at=datetime.now(timezone.utc),
            )

        # Parse valid WorkflowSpec
        if isinstance(workflow_input, WorkflowSpec):
            workflow = workflow_input
        elif isinstance(workflow_input, Path):
            workflow = WorkflowSpec.from_yaml_file(str(workflow_input))
        elif isinstance(workflow_input, str):
            workflow = WorkflowSpec.from_yaml(workflow_input)
        else:
            workflow = WorkflowSpec(**workflow_input)

        # Initialize Ledger
        ledger = ExecutionLedger(run_id=workflow.run_id)
        audit_trail: list[AuditEntry] = []

        total_ladder_attempts = sum(r.max_attempts for r in workflow.ladder)
        max_possible_calls = min(total_ladder_attempts, workflow.policy.max_calls_total)

        final_result: Optional[WorkflowResult] = None

        # Cascade Strategy Loop: Iterate sequentially through rungs
        for rung_idx, rung in enumerate(workflow.ladder):
            rung_attempt_count = 0

            while rung_attempt_count < rung.max_attempts:
                # Check global call budget
                if ledger.total_calls_placed >= workflow.policy.max_calls_total:
                    self.state = WorkflowState.TERMINATED
                    audit_trail.append(
                        AuditEntry(
                            rung=rung.rung,
                            attempt=rung_attempt_count + 1,
                            action="budget_exhausted",
                            details={"total_placed": ledger.total_calls_placed, "limit": workflow.policy.max_calls_total},
                        )
                    )
                    break

                attempt_index = rung_attempt_count + 1

                # Step 2: PREFLIGHT Safety Gate
                self.state = WorkflowState.PREFLIGHT
                preflight = self.safety_engine.preflight_check(
                    workflow=workflow,
                    rung=rung,
                    attempt_num=attempt_index,
                    ledger=ledger,
                    current_dt=now_utc,
                )

                if not preflight.passed:
                    self.state = WorkflowState.BLOCKED
                    audit_trail.append(
                        AuditEntry(
                            rung=rung.rung,
                            attempt=attempt_index,
                            action="preflight_failed",
                            details={"reason": preflight.reason, "failed_check": preflight.failed_check},
                        )
                    )
                    # If suppression or kill switch, terminate entirely fail-closed
                    if preflight.failed_check in {"kill_switch", "suppression"}:
                        final_result = WorkflowResult(
                            run_id=workflow.run_id,
                            status="blocked",
                            outcome="blocked",
                            summary=f"Workflow blocked by safety gate: {preflight.reason}",
                            calls_placed=ledger.total_calls_placed,
                            calls_avoided=max(0, max_possible_calls - ledger.total_calls_placed),
                            started_at=start_time,
                            completed_at=datetime.now(timezone.utc),
                            audit=audit_trail,
                        )
                        self._writeback_if_configured(workflow, final_result)
                        return final_result
                    else:
                        # Rung skipped due to quiet hours or specific preflight issue, escalate to next rung
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
                    audit_trail.append(
                        AuditEntry(
                            rung=rung.rung,
                            attempt=attempt_index,
                            action="plan_rejected",
                            details={"reason": inspection.reason, "violations": inspection.domain_violations},
                        )
                    )
                    final_result = WorkflowResult(
                        run_id=workflow.run_id,
                        status="blocked",
                        outcome="error",
                        summary=f"CallPlan rejected by post-plan safety inspection: {inspection.reason}",
                        calls_placed=ledger.total_calls_placed,
                        calls_avoided=max(0, max_possible_calls - ledger.total_calls_placed),
                        started_at=start_time,
                        completed_at=datetime.now(timezone.utc),
                        audit=audit_trail,
                    )
                    self._writeback_if_configured(workflow, final_result)
                    return final_result

                self.state = WorkflowState.PLAN_APPROVED

                # Step 5: RUNNING
                self.state = WorkflowState.RUNNING
                attempt = CallAttempt(
                    run_id=workflow.run_id,
                    rung=rung.rung,
                    attempt=attempt_index,
                    phone=rung.phone,
                    status="executing",
                    plan=call_plan,
                )
                ledger.record_attempt(attempt)
                call_run = self.adapter.run(call_plan)
                attempt.run = call_run
                ledger.total_calls_placed += 1
                rung_attempt_count += 1

                audit_trail.append(
                    AuditEntry(
                        rung=rung.rung,
                        attempt=attempt_index,
                        action="call_started",
                        details={"external_call_id": call_run.external_call_id, "phone": rung.phone},
                    )
                )

                # Step 6: RESULT_RECEIVED (Bounded Polling)
                self.state = WorkflowState.RESULT_RECEIVED
                status_res = self.adapter.status(call_run.run_id)
                # If adapter supports async delay, poll until terminal
                poll_max = 5
                polls = 0
                while status_res.status == "running" and polls < poll_max:
                    polls += 1
                    status_res = self.adapter.status(call_run.run_id)

                call_run.status = status_res.status
                call_run.structured_result = status_res.structured_result
                call_run.error = status_res.error

                # Step 7: RESULT_VALIDATED
                self.state = WorkflowState.RESULT_VALIDATED
                raw_structured = status_res.structured_result or {}

                # Validate structured result against JSON Schema
                validation_errors: list[str] = []
                schema_valid = True
                if workflow.outcome.result_schema:
                    try:
                        jsonschema.validate(instance=raw_structured, schema=workflow.outcome.result_schema)
                    except jsonschema.ValidationError as val_err:
                        schema_valid = False
                        validation_errors.append(val_err.message)
                    except Exception as err:
                        schema_valid = False
                        validation_errors.append(str(err))

                # Evaluate stop condition
                stop_met = False
                if schema_valid and workflow.outcome.stop_when:
                    stop_met = evaluate_stop_condition(workflow.outcome.stop_when, raw_structured)

                # Extract outcome class
                raw_fixture = status_res.raw_data or {}
                outcome_class = raw_fixture.get("outcome_class")
                if not outcome_class:
                    if status_res.status == "failed":
                        outcome_class = "error"
                    elif raw_structured.get("decision"):
                        outcome_class = str(raw_structured["decision"])
                    else:
                        outcome_class = "unknown"

                evidence_data = raw_fixture.get("evidence")
                evidence_obj = Evidence(**evidence_data) if evidence_data else None

                outcome_result = OutcomeResult(
                    outcome_class=outcome_class,
                    decision=raw_structured.get("decision"),
                    structured_result=raw_structured,
                    result_validation={"valid": schema_valid, "errors": validation_errors},
                    evidence=evidence_obj,
                    stop_condition_met=stop_met,
                )

                attempt.outcome = outcome_result
                attempt.status = "terminal"
                ledger.record_attempt(attempt)

                audit_trail.append(
                    AuditEntry(
                        rung=rung.rung,
                        attempt=attempt_index,
                        action="result_validated",
                        outcome_class=outcome_class,
                        details={
                            "stop_condition_met": stop_met,
                            "schema_valid": schema_valid,
                            "decision": raw_structured.get("decision"),
                        },
                    )
                )

                # Routing Decision Branch
                if stop_met:
                    # Outcome successfully closed!
                    self.state = WorkflowState.CLOSED
                    calls_avoided = max(0, max_possible_calls - ledger.total_calls_placed)
                    ledger.total_calls_avoided = calls_avoided

                    audit_trail.append(
                        AuditEntry(
                            rung=rung.rung,
                            attempt=attempt_index,
                            action="closed",
                            outcome_class=outcome_class,
                            details={"closed_on_rung": rung.rung, "calls_avoided": calls_avoided},
                        )
                    )

                    final_result = WorkflowResult(
                        run_id=workflow.run_id,
                        status="closed",
                        outcome=outcome_class,
                        summary=f"Outcome '{workflow.outcome.name}' successfully closed on rung '{rung.rung}'.",
                        structured_result=raw_structured,
                        result_validation={"valid": schema_valid, "errors": validation_errors},
                        closed_on_rung=rung.rung,
                        attempt_index=attempt_index,
                        calls_placed=ledger.total_calls_placed,
                        calls_avoided=calls_avoided,
                        external_call_id=call_run.external_call_id,
                        recipient_phone_e164=rung.phone,
                        started_at=start_time,
                        completed_at=datetime.now(timezone.utc),
                        audit=audit_trail,
                    )
                    self._writeback_if_configured(workflow, final_result)
                    return final_result

                # Check for Hard Refusal: stop chain and register suppression
                if outcome_class == "hard_refusal" or raw_structured.get("decision") == "hard_refusal":
                    self.safety_engine.add_to_suppression(rung.phone)
                    if workflow.policy.on_hard_refusal == "stop_chain":
                        self.state = WorkflowState.TERMINATED
                        calls_avoided = max(0, max_possible_calls - ledger.total_calls_placed)
                        audit_trail.append(
                            AuditEntry(
                                rung=rung.rung,
                                attempt=attempt_index,
                                action="suppressed_and_terminated",
                                outcome_class="hard_refusal",
                                details={"policy": "stop_chain"},
                            )
                        )
                        final_result = WorkflowResult(
                            run_id=workflow.run_id,
                            status="terminated",
                            outcome="hard_refusal",
                            summary=f"Workflow permanently stopped due to explicit refusal. Phone {rung.phone} added to suppression registry.",
                            structured_result=raw_structured,
                            result_validation={"valid": schema_valid, "errors": validation_errors},
                            closed_on_rung=rung.rung,
                            attempt_index=attempt_index,
                            calls_placed=ledger.total_calls_placed,
                            calls_avoided=calls_avoided,
                            external_call_id=call_run.external_call_id,
                            recipient_phone_e164=rung.phone,
                            started_at=start_time,
                            completed_at=datetime.now(timezone.utc),
                            audit=audit_trail,
                        )
                        self._writeback_if_configured(workflow, final_result)
                        return final_result

                # Check Voicemail Routing Directive
                if outcome_class == "voicemail" and workflow.policy.on_voicemail == "next_rung":
                    self.state = WorkflowState.NEXT_RUNG
                    break  # Break inner loop, escalate immediately to next rung

                # Check Wrong Person Routing Directive
                if outcome_class == "wrong_person" and workflow.policy.on_wrong_person in {"transfer_then_next", "next_rung"}:
                    self.state = WorkflowState.NEXT_RUNG
                    break  # Break inner loop, advance to next rung

                # Check Error Routing Directive
                if outcome_class == "error" and workflow.policy.on_error == "fail_closed":
                    self.state = WorkflowState.TERMINATED
                    break

                # If no_answer or retry needed, loop continues if rung_attempt_count < max_attempts
                if rung_attempt_count < rung.max_attempts:
                    self.state = WorkflowState.RETRY
                else:
                    self.state = WorkflowState.NEXT_RUNG

            # If terminated globally or budget exhausted, stop outer ladder iteration
            if self.state == WorkflowState.TERMINATED:
                break

        # If loop exits without closing
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
            calls_placed=ledger.total_calls_placed,
            calls_avoided=0,
            external_call_id=None,
            recipient_phone_e164=None,
            started_at=start_time,
            completed_at=datetime.now(timezone.utc),
            audit=audit_trail,
        )
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
            # Writeback failures should not crash workflow execution
            pass


def execute_workflow(
    workflow: WorkflowSpec | str | dict[str, Any] | Path,
    adapter: Optional[CalleAdapterBase] = None,
    current_dt: Optional[datetime] = None,
) -> WorkflowResult:
    """Convenience function to run a CloseLoop workflow with default orchestration."""
    engine = OrchestrationEngine(adapter=adapter)
    return engine.run(workflow, current_dt=current_dt)
