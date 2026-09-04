"""Tests for CloseLoop CALL-E Adapter and FakeAdapter (Phase 4)."""

import pytest
from closeloop.adapter import (
    ALL_OUTCOME_CLASSES,
    FakeAdapter,
    PlanRequest,
)
from closeloop.safety import SafetyViolationError


def test_fake_adapter_auth_and_tools():
    # Test authenticated
    adapter = FakeAdapter()
    auth = adapter.auth_status()
    assert auth.authenticated is True
    assert auth.cached is True

    # Test unauthenticated configuration
    unauth_adapter = FakeAdapter(authenticated=False)
    unauth = unauth_adapter.auth_status()
    assert unauth.authenticated is False

    # Test tools check
    tools = adapter.tools_check()
    assert tools.available is True
    assert "plan_call" in tools.tools
    assert "run_call" in tools.tools
    assert "get_call_run" in tools.tools


def test_fake_adapter_planning():
    adapter = FakeAdapter()
    req = PlanRequest(
        run_id="placement-2026-slot-114",
        rung="candidate",
        phone="+15550101234",
        goal="Confirm interview availability for 3 PM IST",
        language="English",
    )
    plan = adapter.plan(req)
    assert plan.plan_id.startswith("plan_")
    assert plan.rung == "candidate"
    assert plan.phone == "+15550101234"
    assert plan.approved is False  # Must start unapproved before inspection


def test_fake_adapter_inspection_approval():
    adapter = FakeAdapter()
    req = PlanRequest(
        run_id="placement-2026-slot-114",
        rung="candidate",
        phone="+15550101234",
        goal="Confirm interview availability for 3 PM IST",
    )
    plan = adapter.plan(req)
    assert plan.approved is False

    # Perform mandatory inspection
    inspection = adapter.inspect_plan(plan)
    assert inspection.approved is True
    assert inspection.reason is None
    assert len(inspection.domain_violations) == 0
    assert plan.approved is True


def test_fake_adapter_inspection_rejection_prohibited_domain():
    adapter = FakeAdapter()
    req = PlanRequest(
        run_id="illegal-call-run",
        rung="patient",
        phone="+15550101234",
        goal="Diagnose candidate symptoms and prescribe antibiotic treatment plan",
    )
    plan = adapter.plan(req)
    inspection = adapter.inspect_plan(plan)

    assert inspection.approved is False
    assert "prohibited domain" in inspection.reason.lower()
    assert "medical_advice" in inspection.domain_violations
    assert plan.approved is False


def test_fake_adapter_run_fails_closed_without_inspection():
    adapter = FakeAdapter()
    req = PlanRequest(
        run_id="unapproved-run",
        rung="candidate",
        phone="+15550101234",
        goal="Confirm interview availability for 3 PM IST",
    )
    plan = adapter.plan(req)
    assert plan.approved is False

    # Invariant 4 enforcement: calling run() on unapproved plan MUST raise SafetyViolationError
    with pytest.raises(SafetyViolationError) as exc_info:
        adapter.run(plan)
    assert "Invariant 4 Violation" in str(exc_info.value)


@pytest.mark.parametrize("outcome_class", ALL_OUTCOME_CLASSES)
def test_fake_adapter_all_11_outcome_fixtures(outcome_class: str):
    adapter = FakeAdapter(default_outcome=outcome_class)
    req = PlanRequest(
        run_id=f"test-run-{outcome_class}",
        rung="candidate",
        phone="+15550101234",
        goal="Confirm interview slot availability",
    )
    plan = adapter.plan(req)
    inspection = adapter.inspect_plan(plan)
    assert inspection.approved is True

    run = adapter.run(plan)
    assert run.run_id.startswith("run_")
    assert run.external_call_id.startswith("calle_fake_")

    # Poll status
    status = adapter.status(run.run_id)
    assert status.run_id == run.run_id

    fixture = FakeAdapter.FIXTURE_MATRIX[outcome_class]
    assert status.status == fixture["status"]
    if fixture["status"] == "failed":
        assert status.error is not None
    else:
        assert status.structured_result is not None


def test_fake_adapter_bounded_polling():
    adapter = FakeAdapter(default_outcome="confirmed", poll_delay_steps=2)
    req = PlanRequest(
        run_id="poll-test",
        rung="candidate",
        phone="+15550101234",
        goal="Confirm interview slot",
    )
    plan = adapter.plan(req)
    adapter.inspect_plan(plan)

    run = adapter.run(plan)
    assert run.status == "running"

    # Poll step 1: still running
    s1 = adapter.status(run.run_id)
    assert s1.status == "running"
    assert s1.structured_result is None

    # Poll step 2: reaches delay step limit and transitions to completed
    s2 = adapter.status(run.run_id)
    assert s2.status == "completed"
    assert s2.structured_result["decision"] == "confirmed"


def test_fake_adapter_rung_and_sequence_routing():
    # Test per-rung outcome mapping
    adapter = FakeAdapter(
        default_outcome="no_answer",
        rung_outcomes={"mentor": "confirmed", "alternate_number": "voicemail"},
    )

    req_cand = PlanRequest(run_id="r1", rung="candidate", phone="+15550101234", goal="Confirm slot")
    plan_cand = adapter.plan(req_cand)
    adapter.inspect_plan(plan_cand)
    run_cand = adapter.run(plan_cand)
    assert adapter.status(run_cand.run_id).raw_data["outcome_class"] == "no_answer"

    req_mentor = PlanRequest(run_id="r2", rung="mentor", phone="+15550101236", goal="Confirm slot")
    plan_mentor = adapter.plan(req_mentor)
    adapter.inspect_plan(plan_mentor)
    run_mentor = adapter.run(plan_mentor)
    assert adapter.status(run_mentor.run_id).raw_data["outcome_class"] == "confirmed"

    # Test sequential outcomes
    seq_adapter = FakeAdapter(sequence_outcomes=["voicemail", "confirmed"])
    p1 = seq_adapter.plan(req_cand)
    seq_adapter.inspect_plan(p1)
    r1 = seq_adapter.run(p1)
    assert seq_adapter.status(r1.run_id).raw_data["outcome_class"] == "voicemail"

    p2 = seq_adapter.plan(req_cand)
    seq_adapter.inspect_plan(p2)
    r2 = seq_adapter.run(p2)
    assert seq_adapter.status(r2.run_id).raw_data["outcome_class"] == "confirmed"
