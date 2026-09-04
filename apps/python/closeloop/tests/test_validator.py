"""Tests for CloseLoop Standalone Workflow Specification Validator (Phase 3)."""

import pytest
from closeloop.validator import (
    WorkflowValidator,
    validate_workflow_spec,
)

VALID_PLACEMENT_YAML = """
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
      preferred_slot:
        type: string
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
  on_callback_requested: schedule_retry
  on_wrong_person: transfer_then_next
  on_hard_refusal: stop_chain
  on_no_answer: retry_then_next
  on_error: fail_closed

writeback:
  target: csv
  path: ./out/results.csv
"""


def test_validator_valid_yaml():
    report = validate_workflow_spec(VALID_PLACEMENT_YAML)
    assert report.ok is True
    assert len(report.errors) == 0
    assert report.value is not None
    assert report.value["run_id"] == "placement-2026-slot-114"
    assert report.value["outcome"]["name"] == "interview_slot_confirmation"

    # Verify canonical Section 33 output format
    output_dict = report.to_dict()
    assert output_dict["ok"] is True
    assert "value" in output_dict
    assert "errors" not in output_dict


def test_validator_missing_run_id():
    yaml_content = """
outcome:
  name: interview_slot_confirmation
ladder:
  - rung: candidate
    phone: "+15550101234"
    consent_basis: "explicit opt-in via form"
"""
    report = validate_workflow_spec(yaml_content)
    assert report.ok is False
    assert any(err.path == "run_id" and err.code == "RUN_ID_REQUIRED" for err in report.errors)

    out = report.to_dict()
    assert out["ok"] is False
    assert len(out["errors"]) >= 1


def test_validator_invalid_run_id_characters():
    spec = {
        "run_id": "invalid run id with spaces & symbols!",
        "outcome": {"name": "test"},
        "ladder": [{"rung": "r1", "phone": "+15550101234", "consent_basis": "opt-in portal"}],
    }
    report = validate_workflow_spec(spec)
    assert report.ok is False
    assert any(err.path == "run_id" and err.code == "INVALID_RUN_ID_FORMAT" for err in report.errors)


def test_validator_invalid_timezone():
    spec = {
        "run_id": "test-run",
        "outcome": {
          "name": "test",
          "quiet_hours": {
            "start": "21:00",
            "end": "09:00",
            "timezone": "Invalid/Fake_Timezone",
          },
        },
        "ladder": [{"rung": "r1", "phone": "+15550101234", "consent_basis": "opt-in portal"}],
    }
    report = validate_workflow_spec(spec)
    assert report.ok is False
    assert any(err.path == "outcome.quiet_hours.timezone" and err.code == "INVALID_TIMEZONE" for err in report.errors)


def test_validator_invalid_quiet_hours_time_format():
    spec = {
        "run_id": "test-run",
        "outcome": {
          "name": "test",
          "quiet_hours": {
            "start": "25:00",  # Invalid hour
            "end": "09:00",
            "timezone": "Asia/Kolkata",
          },
        },
        "ladder": [{"rung": "r1", "phone": "+15550101234", "consent_basis": "opt-in portal"}],
    }
    report = validate_workflow_spec(spec)
    assert report.ok is False
    assert any(err.path == "outcome.quiet_hours.start" for err in report.errors)


def test_validator_invalid_json_schema():
    spec = {
        "run_id": "test-run",
        "outcome": {
          "name": "test",
          "result_schema": {
            "type": "invalid_type_not_in_json_schema",
          },
        },
        "ladder": [{"rung": "r1", "phone": "+15550101234", "consent_basis": "opt-in portal"}],
    }
    report = validate_workflow_spec(spec)
    assert report.ok is False
    assert any(err.path == "outcome.result_schema" and err.code == "INVALID_JSON_SCHEMA" for err in report.errors)


def test_validator_missing_and_empty_ladder():
    spec_missing = {
        "run_id": "test-run",
        "outcome": {"name": "test"},
    }
    report_missing = validate_workflow_spec(spec_missing)
    assert report_missing.ok is False
    assert any(err.path == "ladder" and err.code == "LADDER_REQUIRED" for err in report_missing.errors)

    spec_empty = {
        "run_id": "test-run",
        "outcome": {"name": "test"},
        "ladder": [],
    }
    report_empty = validate_workflow_spec(spec_empty)
    assert report_empty.ok is False
    assert any(err.path == "ladder" and err.code == "EMPTY_LADDER" for err in report_empty.errors)


def test_validator_duplicate_rung_in_ladder():
    spec = {
        "run_id": "test-run",
        "outcome": {"name": "test"},
        "ladder": [
          {"rung": "candidate", "phone": "+15550101234", "consent_basis": "opt-in consent form"},
          {"rung": "candidate", "phone": "+15550101235", "consent_basis": "opt-in consent form"},
        ],
    }
    report = validate_workflow_spec(spec)
    assert report.ok is False
    assert any(err.path == "ladder[1].rung" and err.code == "DUPLICATE_RUNG" for err in report.errors)


def test_validator_invalid_phone_e164():
    spec = {
        "run_id": "test-run",
        "outcome": {"name": "test"},
        "ladder": [
          {"rung": "r1", "phone": "15550101234", "consent_basis": "opt-in consent form"},  # Missing +
        ],
    }
    report = validate_workflow_spec(spec)
    assert report.ok is False
    assert any(err.path == "ladder[0].phone" and err.code == "INVALID_PHONE_E164" for err in report.errors)


def test_validator_missing_consent_basis():
    spec = {
        "run_id": "test-run",
        "outcome": {"name": "test"},
        "ladder": [
          {"rung": "r1", "phone": "+15550101234", "consent_basis": "valid consent"},
          {"rung": "r2", "phone": "+15550101235", "consent_basis": ""},  # Missing consent
        ],
    }
    report = validate_workflow_spec(spec)
    assert report.ok is False
    # Path should specifically point to ladder[1].consent_basis per Section 33 spec
    assert any(err.path == "ladder[1].consent_basis" for err in report.errors)


def test_validator_illegal_call_budget():
    spec = {
        "run_id": "test-run",
        "outcome": {"name": "test"},
        "ladder": [{"rung": "r1", "phone": "+15550101234", "consent_basis": "valid consent"}],
        "policy": {"max_calls_total": 0},  # Must be >= 1
    }
    report = validate_workflow_spec(spec)
    assert report.ok is False
    assert any(err.path == "policy.max_calls_total" and err.code == "INVALID_CALL_BUDGET" for err in report.errors)


def test_validator_budget_warning():
    spec = {
        "run_id": "test-run",
        "outcome": {"name": "test"},
        "ladder": [
          {"rung": "r1", "phone": "+15550101234", "consent_basis": "valid consent", "max_attempts": 3},
          {"rung": "r2", "phone": "+15550101235", "consent_basis": "valid consent", "max_attempts": 3},
        ],
        "policy": {"max_calls_total": 4},  # Total potential attempts (6) > max_calls_total (4)
    }
    report = validate_workflow_spec(spec)
    assert report.ok is True
    assert len(report.warnings) >= 1
    assert "exceeds policy.max_calls_total" in report.warnings[0]


def test_validator_unknown_policy_action():
    spec = {
        "run_id": "test-run",
        "outcome": {"name": "test"},
        "ladder": [{"rung": "r1", "phone": "+15550101234", "consent_basis": "valid consent"}],
        "policy": {"on_voicemail": "unsupported_action_here"},
    }
    report = validate_workflow_spec(spec)
    assert report.ok is False
    assert any(err.path == "policy.on_voicemail" and err.code == "INVALID_POLICY_ACTION" for err in report.errors)


def test_validator_invalid_writeback():
    spec = {
        "run_id": "test-run",
        "outcome": {"name": "test"},
        "ladder": [{"rung": "r1", "phone": "+15550101234", "consent_basis": "valid consent"}],
        "writeback": {"target": "unknown_target"},
    }
    report = validate_workflow_spec(spec)
    assert report.ok is False
    assert any(err.path == "writeback.target" and err.code == "INVALID_WRITEBACK_TARGET" for err in report.errors)
