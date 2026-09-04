"""Tests for CloseLoop Safety Engine (Phase 5)."""

import os
from datetime import datetime, timezone
import pytest

from closeloop.models import (
    CallAttempt,
    CallPlan,
    ContactRung,
    ExecutionLedger,
    OutcomeContract,
    Policy,
    QuietHoursConfig,
    WorkflowSpec,
)
from closeloop.safety import (
    ConsentMissingError,
    KillSwitchActiveError,
    QuietHoursViolationError,
    SuppressionViolationError,
)
from closeloop.safety_engine import (
    BudgetExhaustedError,
    IdempotencyCollisionError,
    PlanInspectionError,
    RecipientMismatchError,
    SafetyEngine,
)


@pytest.fixture
def sample_workflow():
    return WorkflowSpec(
        run_id="placement-2026-slot-114",
        outcome=OutcomeContract(
            name="interview_slot_confirmation",
            quiet_hours=QuietHoursConfig(start="21:00", end="09:00", timezone="Asia/Kolkata"),
        ),
        ladder=[
            ContactRung(
                rung="candidate",
                phone="+15550101234",
                consent_basis="candidate opted in during placement registration",
                max_attempts=2,
            ),
            ContactRung(
                rung="alternate_number",
                phone="+15550101235",
                consent_basis="alternate number supplied by candidate",
                max_attempts=1,
            ),
        ],
        policy=Policy(max_calls_total=3),
    )


def test_preflight_all_checks_pass(sample_workflow):
    engine = SafetyEngine()
    rung = sample_workflow.ladder[0]
    ledger = ExecutionLedger(run_id=sample_workflow.run_id)

    # 13:30 IST is outside quiet hours (21:00 - 09:00)
    current_dt = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)
    result = engine.preflight_check(sample_workflow, rung, attempt_num=1, ledger=ledger, current_dt=current_dt)

    assert result.passed is True
    assert result.failed_check is None
    assert result.recipient_masked == "+1555010****"
    assert result.checks["kill_switch"] is True
    assert result.checks["suppression"] is True
    assert result.checks["consent"] is True
    assert result.checks["e164"] is True
    assert result.checks["quiet_hours"] is True
    assert result.checks["budget"] is True
    assert result.checks["idempotency"] is True


def test_preflight_fails_on_kill_switch(sample_workflow, monkeypatch):
    monkeypatch.setenv("CLOSELOOP_KILL_SWITCH", "1")
    engine = SafetyEngine()
    rung = sample_workflow.ladder[0]

    result = engine.preflight_check(sample_workflow, rung, attempt_num=1)
    assert result.passed is False
    assert result.failed_check == "kill_switch"

    with pytest.raises(KillSwitchActiveError):
        engine.assert_preflight(sample_workflow, rung, attempt_num=1)


def test_preflight_fails_on_suppression(sample_workflow):
    engine = SafetyEngine(suppression_registry=["+15550101234"])
    rung = sample_workflow.ladder[0]

    result = engine.preflight_check(sample_workflow, rung, attempt_num=1)
    assert result.passed is False
    assert result.failed_check == "suppression"
    assert "suppression list" in result.reason.lower()

    with pytest.raises(SuppressionViolationError):
        engine.assert_preflight(sample_workflow, rung, attempt_num=1)


def test_preflight_fails_on_quiet_hours(sample_workflow):
    engine = SafetyEngine()
    rung = sample_workflow.ladder[0]

    # 03:30 IST is within quiet hours (21:00 - 09:00)
    current_dt = datetime(2026, 9, 4, 22, 0, tzinfo=timezone.utc)
    result = engine.preflight_check(sample_workflow, rung, attempt_num=1, current_dt=current_dt)

    assert result.passed is False
    assert result.failed_check == "quiet_hours"
    assert "quiet hours" in result.reason.lower()

    with pytest.raises(QuietHoursViolationError):
        engine.assert_preflight(sample_workflow, rung, attempt_num=1, current_dt=current_dt)


def test_preflight_fails_on_budget_exhausted(sample_workflow):
    engine = SafetyEngine()
    rung = sample_workflow.ladder[0]
    ledger = ExecutionLedger(run_id=sample_workflow.run_id, total_calls_placed=3)  # max_calls_total is 3

    current_dt = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)
    result = engine.preflight_check(sample_workflow, rung, attempt_num=1, ledger=ledger, current_dt=current_dt)

    assert result.passed is False
    assert result.failed_check == "budget"
    assert "budget exhausted" in result.reason.lower()

    with pytest.raises(BudgetExhaustedError):
        engine.assert_preflight(sample_workflow, rung, attempt_num=1, ledger=ledger, current_dt=current_dt)


def test_preflight_fails_on_idempotency_collision(sample_workflow):
    engine = SafetyEngine()
    rung = sample_workflow.ladder[0]
    ledger = ExecutionLedger(run_id=sample_workflow.run_id)

    # Record a terminal attempt for rung="candidate", attempt=1
    attempt = CallAttempt(
        run_id=sample_workflow.run_id,
        rung="candidate",
        attempt=1,
        phone=rung.phone,
        status="terminal",
    )
    ledger.record_attempt(attempt)

    current_dt = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)
    result = engine.preflight_check(sample_workflow, rung, attempt_num=1, ledger=ledger, current_dt=current_dt)

    assert result.passed is False
    assert result.failed_check == "idempotency"
    assert "terminal idempotency collision" in result.reason.lower()

    with pytest.raises(IdempotencyCollisionError):
        engine.assert_preflight(sample_workflow, rung, attempt_num=1, ledger=ledger, current_dt=current_dt)


def test_post_plan_inspection_clean_passes(sample_workflow):
    engine = SafetyEngine()
    intended_rung = sample_workflow.ladder[0]

    plan = CallPlan(
        plan_id="plan_xyz",
        rung="candidate",
        phone="+15550101234",
        goal="Confirm interview attendance for 3 PM IST slot",
        script="Hello, calling regarding your interview appointment.",
    )
    result = engine.inspect_plan(plan, intended_rung, sample_workflow)

    assert result.approved is True
    assert result.reason is None
    assert len(result.domain_violations) == 0
    assert result.recipient_verified is True
    assert result.goal_verified is True
    assert plan.approved is True


def test_post_plan_inspection_fails_on_recipient_mismatch(sample_workflow):
    engine = SafetyEngine()
    intended_rung = sample_workflow.ladder[0]

    # Plan has a phone differing from intended rung
    plan_wrong_phone = CallPlan(
        plan_id="plan_wrong_phone",
        rung="candidate",
        phone="+15550109999",  # Different number!
        goal="Confirm interview slot",
    )
    res_phone = engine.inspect_plan(plan_wrong_phone, intended_rung, sample_workflow)
    assert res_phone.approved is False
    assert res_phone.recipient_verified is False
    assert "phone" in res_phone.reason.lower()

    with pytest.raises(RecipientMismatchError):
        engine.assert_plan_inspection(plan_wrong_phone, intended_rung, sample_workflow)

    # Plan has a rung differing from intended rung
    plan_wrong_rung = CallPlan(
        plan_id="plan_wrong_rung",
        rung="alternate_number",  # Mismatched rung!
        phone="+15550101234",
        goal="Confirm interview slot",
    )
    res_rung = engine.inspect_plan(plan_wrong_rung, intended_rung, sample_workflow)
    assert res_rung.approved is False
    assert res_rung.recipient_verified is False
    assert "rung identifier" in res_rung.reason.lower()

    with pytest.raises(RecipientMismatchError):
        engine.assert_plan_inspection(plan_wrong_rung, intended_rung, sample_workflow)


def test_post_plan_inspection_fails_on_prohibited_domain(sample_workflow):
    engine = SafetyEngine()
    intended_rung = sample_workflow.ladder[0]

    plan_medical = CallPlan(
        plan_id="plan_prohibited",
        rung="candidate",
        phone="+15550101234",
        goal="Diagnose medical symptoms and prescribe medication",
    )
    result = engine.inspect_plan(plan_medical, intended_rung, sample_workflow)

    assert result.approved is False
    assert result.goal_verified is False
    assert "medical_advice" in result.domain_violations
    assert plan_medical.approved is False

    with pytest.raises(PlanInspectionError):
        engine.assert_plan_inspection(plan_medical, intended_rung, sample_workflow)


def test_suppression_addition():
    engine = SafetyEngine()
    assert engine.is_suppressed("+15550101234") is False

    engine.add_to_suppression("+15550101234")
    assert engine.is_suppressed("+15550101234") is True
