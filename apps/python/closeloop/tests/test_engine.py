"""Tests for CloseLoop Orchestration Engine (Phase 6)."""

import csv
import json
from datetime import datetime, timezone
import pytest

from closeloop.adapter import FakeAdapter
from closeloop.engine import (
    OrchestrationEngine,
    WorkflowState,
    evaluate_stop_condition,
    execute_workflow,
)
from closeloop.models import (
    ContactRung,
    OutcomeContract,
    Policy,
    QuietHoursConfig,
    WorkflowSpec,
    WritebackConfig,
)
from closeloop.safety_engine import SafetyEngine


@pytest.fixture
def standard_cascade_workflow():
    return WorkflowSpec(
        run_id="placement-2026-slot-114",
        outcome=OutcomeContract(
            name="interview_slot_confirmation",
            result_schema={
                "type": "object",
                "required": ["decision"],
                "properties": {
                    "decision": {"type": "string", "enum": ["confirmed", "reschedule", "declined", "unreachable"]},
                    "preferred_slot": {"type": "string"},
                },
            },
            stop_when="decision in [confirmed, declined]",
        ),
        ladder=[
            ContactRung(
                rung="candidate",
                phone="+15550101234",
                consent_basis="candidate opted in during placement registration",
                max_attempts=1,
            ),
            ContactRung(
                rung="alternate_number",
                phone="+15550101235",
                consent_basis="alternate number supplied by candidate",
                max_attempts=1,
            ),
            ContactRung(
                rung="mentor",
                phone="+15550101236",
                consent_basis="authorized coordinator contact",
                max_attempts=1,
            ),
        ],
        policy=Policy(
            max_calls_total=3,
            on_voicemail="next_rung",
            on_hard_refusal="stop_chain",
        ),
    )


def test_stop_condition_evaluation():
    # String expression with 'in'
    expr = "decision in [confirmed, declined]"
    assert evaluate_stop_condition(expr, {"decision": "confirmed"}) is True
    assert evaluate_stop_condition(expr, {"decision": "declined"}) is True
    assert evaluate_stop_condition(expr, {"decision": "reschedule"}) is False
    assert evaluate_stop_condition(expr, {"decision": "voicemail"}) is False

    # String expression with '=='
    eq_expr = "decision == confirmed"
    assert evaluate_stop_condition(eq_expr, {"decision": "confirmed"}) is True
    assert evaluate_stop_condition(eq_expr, {"decision": "declined"}) is False

    # Dict rule syntax
    dict_rule = {"field": "decision", "in": ["confirmed", "reschedule"]}
    assert evaluate_stop_condition(dict_rule, {"decision": "reschedule"}) is True
    assert evaluate_stop_condition(dict_rule, {"decision": "declined"}) is False


def test_engine_cascade_closes_on_first_rung(standard_cascade_workflow):
    # Candidate immediately confirms
    adapter = FakeAdapter(default_outcome="confirmed")
    engine = OrchestrationEngine(adapter=adapter)

    result = engine.run(standard_cascade_workflow)

    assert result.status == "closed"
    assert result.outcome == "confirmed"
    assert result.closed_on_rung == "candidate"
    assert result.attempt_index == 1
    assert result.calls_placed == 1
    # 3 total ladder attempts possible, closed after 1 call -> 2 calls avoided!
    assert result.calls_avoided == 2
    assert result.structured_result["decision"] == "confirmed"
    assert result.result_validation["valid"] is True
    assert len(result.audit) > 0


def test_engine_cascade_escalates_across_ladder(standard_cascade_workflow):
    # candidate -> voicemail, alternate_number -> no_answer, mentor -> confirmed
    adapter = FakeAdapter(
        rung_outcomes={
            "candidate": "voicemail",
            "alternate_number": "no_answer",
            "mentor": "confirmed",
        }
    )
    engine = OrchestrationEngine(adapter=adapter)

    result = engine.run(standard_cascade_workflow)

    assert result.status == "closed"
    assert result.outcome == "confirmed"
    assert result.closed_on_rung == "mentor"
    assert result.calls_placed == 3
    assert result.calls_avoided == 0

    # Verify audit trail shows progression through all 3 rungs
    rungs_contacted = [a.rung for a in result.audit if a.action == "call_started"]
    assert rungs_contacted == ["candidate", "alternate_number", "mentor"]


def test_engine_hard_refusal_stops_chain_and_suppresses(standard_cascade_workflow):
    # Candidate explicitly refuses
    adapter = FakeAdapter(default_outcome="hard_refusal")
    safety_engine = SafetyEngine()
    engine = OrchestrationEngine(adapter=adapter, safety_engine=safety_engine)

    result = engine.run(standard_cascade_workflow)

    assert result.status == "terminated"
    assert result.outcome == "hard_refusal"
    assert result.calls_placed == 1
    # Candidate phone should be placed on suppression list
    assert safety_engine.is_suppressed("+15550101234") is True
    # Alternate number and mentor must NOT have been called
    rungs_contacted = [a.rung for a in result.audit if a.action == "call_started"]
    assert rungs_contacted == ["candidate"]


def test_engine_budget_exhaustion(standard_cascade_workflow):
    # Limit budget to 1 call total
    standard_cascade_workflow.policy.max_calls_total = 1
    adapter = FakeAdapter(default_outcome="voicemail")
    engine = OrchestrationEngine(adapter=adapter)

    result = engine.run(standard_cascade_workflow)

    assert result.status == "not_closed"
    assert result.calls_placed == 1
    assert result.calls_avoided == 0


def test_engine_preflight_blocks_execution(standard_cascade_workflow, monkeypatch):
    monkeypatch.setenv("CLOSELOOP_KILL_SWITCH", "1")
    adapter = FakeAdapter()
    engine = OrchestrationEngine(adapter=adapter)

    result = engine.run(standard_cascade_workflow)

    assert result.status == "blocked"
    assert result.calls_placed == 0
    assert "kill switch" in result.summary.lower()


def test_engine_writeback_csv_and_json(standard_cascade_workflow, tmp_path):
    csv_file = tmp_path / "results.csv"
    json_file = tmp_path / "results.json"

    # Test CSV writeback
    standard_cascade_workflow.writeback = WritebackConfig(target="csv", path=str(csv_file))
    adapter = FakeAdapter(default_outcome="confirmed")
    engine = OrchestrationEngine(adapter=adapter)
    engine.run(standard_cascade_workflow)

    assert csv_file.is_file()
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 1
        assert reader[0]["run_id"] == "placement-2026-slot-114"
        assert reader[0]["status"] == "closed"
        assert reader[0]["closed_on_rung"] == "candidate"

    # Test JSON writeback
    standard_cascade_workflow.writeback = WritebackConfig(target="json", path=str(json_file))
    engine.run(standard_cascade_workflow)

    assert json_file.is_file()
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["run_id"] == "placement-2026-slot-114"
        assert data["status"] == "closed"
        assert data["calls_placed"] == 1
        assert data["calls_avoided"] == 2
