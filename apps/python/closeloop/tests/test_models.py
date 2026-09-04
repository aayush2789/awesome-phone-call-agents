"""Tests for CloseLoop typed domain models (Phase 2)."""

import pytest
from datetime import datetime, timezone
import hashlib

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
from closeloop.safety import ConsentMissingError, E164ValidationError


def test_quiet_hours_config():
    qh = QuietHoursConfig(start="21:00", end="09:00", timezone="Asia/Kolkata")
    assert qh.start == "21:00"
    assert qh.end == "09:00"
    assert qh.timezone == "Asia/Kolkata"

    # Test is_active at specific times
    dt_active = datetime(2026, 9, 4, 22, 0, tzinfo=timezone.utc)  # 03:30 IST
    assert qh.is_active(current_dt=dt_active) is True

    dt_inactive = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)  # 13:30 IST
    assert qh.is_active(current_dt=dt_inactive) is False


def test_outcome_contract():
    contract = OutcomeContract(
        name="interview_slot_confirmation",
        deadline="2026-09-04T18:00:00+05:30",
        quiet_hours=QuietHoursConfig(start="21:00", end="09:00", timezone="Asia/Kolkata"),
        result_schema={
            "type": "object",
            "required": ["decision"],
            "properties": {
                "decision": {"type": "string", "enum": ["confirmed", "reschedule", "declined", "unreachable"]},
            },
        },
        stop_when="decision in [confirmed, declined]",
    )
    assert contract.name == "interview_slot_confirmation"
    assert contract.quiet_hours.timezone == "Asia/Kolkata"
    assert contract.result_schema["required"] == ["decision"]


def test_contact_rung_valid():
    rung = ContactRung(
        rung="candidate",
        phone="+15550101234",
        region="US",
        language="English",
        consent_basis="candidate opted in during placement registration",
        max_attempts=2,
    )
    assert rung.rung == "candidate"
    assert rung.phone == "+15550101234"
    assert rung.max_attempts == 2


def test_contact_rung_invalid_e164():
    with pytest.raises(E164ValidationError):
        ContactRung(
            rung="candidate",
            phone="15550101234",  # Missing + prefix
            consent_basis="explicit opt-in via portal",
        )

    with pytest.raises(E164ValidationError):
        ContactRung(
            rung="candidate",
            phone="+1(555)010-1234",  # Non-digit formatting
            consent_basis="explicit opt-in via portal",
        )


def test_contact_rung_missing_consent():
    with pytest.raises(ConsentMissingError):
        ContactRung(
            rung="candidate",
            phone="+15550101234",
            consent_basis="",  # Empty consent basis
        )

    with pytest.raises(ConsentMissingError):
        ContactRung(
            rung="candidate",
            phone="+15550101234",
            consent_basis="no",  # Too short to be meaningful
        )


def test_policy_defaults_and_overrides():
    default_policy = Policy()
    assert default_policy.max_calls_total == 3
    assert default_policy.on_voicemail == "next_rung"
    assert default_policy.on_callback_requested == "schedule_retry"
    assert default_policy.on_wrong_person == "transfer_then_next"
    assert default_policy.on_hard_refusal == "stop_chain"
    assert default_policy.on_error == "fail_closed"

    custom_policy = Policy(
        max_calls_total=5,
        on_voicemail="retry",
        on_hard_refusal="stop_chain",
    )
    assert custom_policy.max_calls_total == 5
    assert custom_policy.on_voicemail == "retry"


def test_workflow_spec_from_yaml():
    yaml_content = """
run_id: placement-2026-slot-114

outcome:
  name: interview_slot_confirmation
  deadline: "2026-09-04T18:00:00+05:30"
  quiet_hours:
    start: "21:00"
    end: "09:00"
    timezone: Asia/Kolkata
  result_schema:
    type: object
    required:
      - decision
    properties:
      decision:
        type: string
        enum:
          - confirmed
          - reschedule
          - declined
          - unreachable
  stop_when: "decision in [confirmed, declined]"

strategy:
  type: cascade

ladder:
  - rung: candidate
    phone: "+15550101234"
    region: IN
    language: English
    consent_basis: "candidate opted in during placement registration"
    max_attempts: 2

  - rung: alternate_number
    phone: "+15550101235"
    region: IN
    language: English
    consent_basis: "alternate number explicitly supplied by candidate"
    max_attempts: 1

policy:
  max_calls_total: 4
  on_voicemail: next_rung
  on_hard_refusal: stop_chain

writeback:
  target: csv
  path: ./out/results.csv
"""
    spec = WorkflowSpec.from_yaml(yaml_content)
    assert spec.run_id == "placement-2026-slot-114"
    assert spec.outcome.name == "interview_slot_confirmation"
    assert len(spec.ladder) == 2
    assert spec.ladder[0].phone == "+15550101234"
    assert spec.ladder[1].max_attempts == 1
    assert spec.policy.max_calls_total == 4
    assert spec.writeback.target == "csv"
    assert spec.writeback.path == "./out/results.csv"


def test_call_plan_lifecycle():
    plan = CallPlan(
        plan_id="plan_abc_123",
        rung="candidate",
        phone="+15550101234",
        goal="Confirm interview availability for 3 PM IST",
        script="Hello, calling to confirm your interview slot.",
    )
    assert plan.approved is False
    assert plan.rejection_reason is None

    # Test inspection approval
    plan.approve()
    assert plan.approved is True

    # Test rejection
    plan.reject("Goal contains prohibited clinical terminology")
    assert plan.approved is False
    assert "prohibited" in plan.rejection_reason


def test_call_run_record():
    run = CallRun(
        run_id="run_attempt_001",
        external_call_id="calle_external_789",
        status="completed",
        structured_result={"decision": "confirmed", "preferred_slot": "15:00"},
    )
    assert run.run_id == "run_attempt_001"
    assert run.external_call_id == "calle_external_789"
    assert run.status == "completed"
    assert run.structured_result["decision"] == "confirmed"


def test_evidence_provenance():
    excerpt = "Yes, I will definitely be there at 3 PM."
    expected_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()

    evidence = Evidence(
        source="transcript",
        speaker="callee",
        confidence=0.95,
        excerpt=excerpt,
        rationale="Callee clearly affirmed interview attendance.",
    )
    assert evidence.confidence == 0.95
    assert evidence.excerpt_hash == expected_hash
    assert evidence.speaker == "callee"


def test_outcome_result():
    evidence = Evidence(
        source="transcript",
        speaker="callee",
        confidence=0.98,
        excerpt="Confirmed for 3pm",
    )
    outcome = OutcomeResult(
        outcome_class="confirmed",
        decision="confirmed",
        structured_result={"decision": "confirmed", "slot": "15:00"},
        result_validation={"valid": True, "errors": []},
        confidence=0.98,
        evidence=evidence,
        stop_condition_met=True,
    )
    assert outcome.outcome_class == "confirmed"
    assert outcome.stop_condition_met is True
    assert outcome.evidence.confidence == 0.98


def test_call_attempt_and_idempotency_key():
    run_id = "placement-slot-42"
    rung = "candidate"
    attempt_num = 1

    expected_key = compute_idempotency_key(run_id, rung, attempt_num)
    assert len(expected_key) == 64

    attempt = CallAttempt(
        run_id=run_id,
        rung=rung,
        attempt=attempt_num,
        phone="+15550101234",
    )
    assert attempt.idempotency_key == expected_key
    assert attempt.status == "planned"


def test_execution_ledger_lifecycle():
    ledger = ExecutionLedger(run_id="placement-slot-42")

    attempt = CallAttempt(
        run_id=ledger.run_id,
        rung="candidate",
        attempt=1,
        phone="+15550101234",
    )
    ledger.record_attempt(attempt)
    assert len(ledger.attempts) == 1
    assert ledger.is_attempt_terminal("candidate", 1) is False

    # Transition attempt to terminal
    attempt.status = "terminal"
    ledger.record_attempt(attempt)
    assert ledger.is_attempt_terminal("candidate", 1) is True

    # Check non-existent attempt
    assert ledger.is_attempt_terminal("candidate", 2) is False


def test_workflow_result_envelope_formatting():
    audit_entry = AuditEntry(
        rung="candidate",
        attempt=1,
        action="closed",
        outcome_class="confirmed",
        details={"note": "Confirmed without escalation"},
    )

    wf_result = WorkflowResult(
        run_id="placement-2026-slot-114",
        status="closed",
        outcome="confirmed",
        summary="Candidate confirmed the interview slot.",
        structured_result={"decision": "confirmed", "preferred_slot": "2026-09-04T15:00+05:30"},
        result_validation={"valid": True},
        closed_on_rung="candidate",
        attempt_index=1,
        calls_placed=1,
        calls_avoided=2,
        external_call_id="calle_run_999",
        recipient_phone_e164="+15550101234",
        started_at=datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 9, 4, 10, 2, 30, tzinfo=timezone.utc),
        source_platform="closeloop",
        source_object_id="slot-114",
        audit=[audit_entry],
    )

    envelope = wf_result.to_envelope_dict()

    assert envelope["run_id"] == "placement-2026-slot-114"
    assert envelope["status"] == "closed"
    assert envelope["outcome"] == "confirmed"
    assert envelope["calls_placed"] == 1
    assert envelope["calls_avoided"] == 2
    # Phone number must be masked in envelope output per safety invariants
    assert envelope["recipient_phone_e164"] == "+1555010****"
    assert "+15550101234" not in str(envelope)
    assert len(envelope["audit"]) == 1
    assert envelope["audit"][0]["rung"] == "candidate"
    assert envelope["audit"][0]["action"] == "closed"
