"""Unit tests for Phase 8: Persistent Idempotency Ledger (SQLiteLedger)."""

import pytest
from datetime import datetime, timezone
from pathlib import Path

from closeloop.adapter import FakeAdapter
from closeloop.engine import OrchestrationEngine
from closeloop.ledger import DuplicateAttemptError, SQLiteLedger
from closeloop.models import (
    AuditEntry,
    CallAttempt,
    CallPlan,
    CallRun,
    ContactRung,
    OutcomeContract,
    OutcomeResult,
    Policy,
    WorkflowResult,
    WorkflowSpec,
    compute_idempotency_key,
)


@pytest.fixture
def standard_workflow():
    return WorkflowSpec(
        run_id="ledger-run-001",
        outcome=OutcomeContract(
            name="interview_slot_confirmation",
            result_schema={
                "type": "object",
                "required": ["decision"],
                "properties": {
                    "decision": {"type": "string", "enum": ["confirmed", "reschedule", "declined"]},
                },
            },
            stop_when="decision in [confirmed, declined]",
        ),
        ladder=[
            ContactRung(
                rung="primary",
                phone="+15551234567",
                consent_basis="patient explicitly consented to reminders",
                max_attempts=2,
            )
        ],
        policy=Policy(max_calls_total=2),
    )


def test_sqlite_ledger_initialization(tmp_path: Path):
    """Test initialization of both in-memory and disk-backed SQLite ledgers."""
    mem_ledger = SQLiteLedger(":memory:")
    assert mem_ledger.db_path == ":memory:"

    disk_db = tmp_path / "subdir" / "test_ledger.db"
    disk_ledger = SQLiteLedger(disk_db)
    assert disk_db.exists()
    assert disk_ledger.db_path == str(disk_db)


def test_record_and_get_workflow_spec(standard_workflow):
    """Test storing and retrieving a WorkflowSpec in the ledger."""
    ledger = SQLiteLedger(":memory:")

    ledger.record_workflow(standard_workflow)
    retrieved = ledger.get_workflow_spec(standard_workflow.run_id)

    assert retrieved is not None
    assert retrieved.run_id == standard_workflow.run_id
    assert retrieved.outcome.name == "interview_slot_confirmation"
    assert len(retrieved.ladder) == 1
    assert retrieved.ladder[0].phone == "+15551234567"

    # Non-existent spec
    assert ledger.get_workflow_spec("unknown-run") is None


def test_record_attempt_and_enforce_idempotency(standard_workflow):
    """Test recording an attempt and enforcing unique idempotency key constraint."""
    ledger = SQLiteLedger(":memory:")
    ledger.record_workflow(standard_workflow)

    plan = CallPlan(
        plan_id="plan-test-01",
        rung="primary",
        goal="confirm appointment",
        phone="+15551234567",
        prompt="Test call prompt",
        max_duration_seconds=120,
    )
    key = compute_idempotency_key(standard_workflow.run_id, "primary", 1)
    attempt = CallAttempt(
        idempotency_key=key,
        run_id=standard_workflow.run_id,
        rung="primary",
        attempt=1,
        phone="+15551234567",
        plan=plan,
        status="planned",
    )

    assert not ledger.has_attempt(standard_workflow.run_id, "primary", 1)
    ledger.record_attempt(attempt)
    assert ledger.has_attempt(standard_workflow.run_id, "primary", 1)

    fetched = ledger.get_attempt(key)
    assert fetched is not None
    assert fetched.run_id == standard_workflow.run_id
    assert fetched.status == "planned"

    # Second attempt with identical key MUST raise DuplicateAttemptError
    with pytest.raises(DuplicateAttemptError):
        ledger.record_attempt(attempt)


def test_update_attempt_status_transitions(standard_workflow):
    """Test transitioning attempt status: planned -> executing -> terminal."""
    ledger = SQLiteLedger(":memory:")
    ledger.record_workflow(standard_workflow)

    key = compute_idempotency_key(standard_workflow.run_id, "primary", 1)
    plan = CallPlan(
        plan_id="plan-test-02",
        rung="primary",
        goal="confirm appointment",
        phone="+15551234567",
        prompt="Test call prompt",
        max_duration_seconds=120,
    )
    attempt = CallAttempt(
        idempotency_key=key,
        run_id=standard_workflow.run_id,
        rung="primary",
        attempt=1,
        phone="+15551234567",
        plan=plan,
        status="planned",
    )
    ledger.record_attempt(attempt)

    assert not ledger.is_attempt_terminal(standard_workflow.run_id, "primary", 1)

    # Transition to executing
    ledger.update_attempt_status(key, status="executing")
    in_flight = ledger.get_attempt(key)
    assert in_flight is not None
    assert in_flight.status == "executing"
    assert not ledger.is_attempt_terminal(standard_workflow.run_id, "primary", 1)

    # Transition to terminal with run and outcome
    call_run = CallRun(
        run_id=standard_workflow.run_id,
        external_call_id="call-mock-99",
        status="completed",
        structured_result={"decision": "confirmed"},
    )
    outcome = OutcomeResult(
        outcome_class="confirmed",
        decision="confirmed",
        confidence=1.0,
        structured_result={"decision": "confirmed"},
    )

    ledger.update_attempt_status(
        key,
        status="terminal",
        run=call_run,
        outcome=outcome,
    )

    assert ledger.is_attempt_terminal(standard_workflow.run_id, "primary", 1)
    terminal_attempt = ledger.get_attempt(key)
    assert terminal_attempt is not None
    assert terminal_attempt.status == "terminal"
    assert terminal_attempt.run is not None
    assert terminal_attempt.run.external_call_id == "call-mock-99"
    assert terminal_attempt.outcome is not None
    assert terminal_attempt.outcome.outcome_class == "confirmed"


def test_crash_recovery_reconciliation(standard_workflow):
    """Test reconciling attempts left in 'executing' state after an unexpected process crash."""
    ledger = SQLiteLedger(":memory:")
    ledger.record_workflow(standard_workflow)

    key = compute_idempotency_key(standard_workflow.run_id, "primary", 1)
    plan = CallPlan(
        plan_id="plan-test-03",
        rung="primary",
        goal="confirm appointment",
        phone="+15551234567",
        prompt="Test call prompt",
        max_duration_seconds=120,
    )
    attempt = CallAttempt(
        idempotency_key=key,
        run_id=standard_workflow.run_id,
        rung="primary",
        attempt=1,
        phone="+15551234567",
        plan=plan,
        status="planned",
    )
    ledger.record_attempt(attempt)
    ledger.update_attempt_status(key, status="executing")

    # Simulate crash recovery on restart
    reconciled = ledger.reconcile_in_flight_attempts(standard_workflow.run_id)
    assert len(reconciled) == 1
    assert reconciled[0].idempotency_key == key

    # Fetch updated from ledger
    recovered = ledger.get_attempt(key)
    assert recovered is not None
    assert recovered.status == "needs_reconciliation"


def test_audit_trail_logging(standard_workflow):
    """Test persisting and retrieving an ordered audit trail."""
    ledger = SQLiteLedger(":memory:")
    ledger.record_workflow(standard_workflow)
    run_id = standard_workflow.run_id

    e1 = AuditEntry(
        rung="primary",
        attempt=1,
        action="inspect_plan",
        details={"status": "approved"},
    )
    e2 = AuditEntry(
        rung="primary",
        attempt=1,
        action="execute_call",
        details={"call_id": "call-123"},
    )
    e3 = AuditEntry(
        rung="primary",
        attempt=1,
        action="classify_outcome",
        outcome_class="confirmed",
        details={"confidence": 0.95},
    )

    ledger.record_audit(run_id, e1)
    ledger.record_audit(run_id, e2)
    ledger.record_audit(run_id, e3)

    trail = ledger.get_audit_trail(run_id)
    assert len(trail) == 3
    assert [e.action for e in trail] == ["inspect_plan", "execute_call", "classify_outcome"]
    assert trail[2].outcome_class == "confirmed"


def test_save_and_get_workflow_result(standard_workflow):
    """Test persisting and retrieving the final workflow outcome envelope."""
    ledger = SQLiteLedger(":memory:")
    ledger.record_workflow(standard_workflow)

    res = WorkflowResult(
        run_id=standard_workflow.run_id,
        status="closed",
        outcome="confirmed",
        summary="Patient confirmed appointment successfully.",
        calls_placed=1,
        calls_avoided=0,
        recipient_phone_e164="+15551234567",
    )

    assert ledger.get_workflow_result(standard_workflow.run_id) is None
    ledger.save_workflow_result(res)

    fetched = ledger.get_workflow_result(standard_workflow.run_id)
    assert fetched is not None
    assert fetched.run_id == standard_workflow.run_id
    assert fetched.status == "closed"
    assert fetched.outcome == "confirmed"
    assert fetched.summary == "Patient confirmed appointment successfully."
    assert fetched.calls_placed == 1


def test_engine_integration_with_persistent_ledger(standard_workflow, tmp_path: Path):
    """Verify that OrchestrationEngine persists all state, attempts, and results into SQLiteLedger."""
    disk_db = tmp_path / "engine_ledger.db"
    ledger = SQLiteLedger(disk_db)
    adapter = FakeAdapter(default_outcome="confirmed")
    engine = OrchestrationEngine(adapter=adapter, ledger=ledger)

    result = engine.run(standard_workflow)

    assert result.status == "closed"
    assert result.outcome == "confirmed"

    # Verify ledger database contents
    assert ledger.get_workflow_spec(standard_workflow.run_id) is not None
    saved_res = ledger.get_workflow_result(standard_workflow.run_id)
    assert saved_res is not None
    assert saved_res.outcome == "confirmed"
    assert saved_res.status == "closed"

    attempts = ledger.list_attempts(standard_workflow.run_id)
    assert len(attempts) == 1
    assert attempts[0].status == "terminal"
    assert attempts[0].outcome is not None
    assert attempts[0].outcome.outcome_class == "confirmed"

    trail = ledger.get_audit_trail(standard_workflow.run_id)
    actions = [e.action for e in trail]
    assert "call_started" in actions
    assert "result_validated" in actions
    assert "closed" in actions
