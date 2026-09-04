"""Tests for CloseLoop Outcome Classifier (Phase 7)."""

import pytest
from closeloop.adapter import ALL_OUTCOME_CLASSES, CallStatus, FakeAdapter
from closeloop.classifier import OutcomeClassifier
from closeloop.models import OutcomeContract


@pytest.fixture
def classifier():
    return OutcomeClassifier()


@pytest.fixture
def contract():
    return OutcomeContract(
        name="interview_confirmation",
        result_schema={
            "type": "object",
            "required": ["decision"],
            "properties": {
                "decision": {"type": "string", "enum": ["confirmed", "reschedule", "declined"]},
                "preferred_slot": {"type": "string"},
            },
        },
        stop_when="decision in [confirmed, declined]",
    )


@pytest.mark.parametrize("outcome_class", ALL_OUTCOME_CLASSES)
def test_classifier_all_11_fixtures(classifier, contract, outcome_class):
    fixture = FakeAdapter.FIXTURE_MATRIX[outcome_class]
    status = CallStatus(
        run_id=f"run_{outcome_class}",
        status=fixture["status"],
        structured_result=fixture.get("structured_result"),
        error=fixture.get("error"),
        raw_data={"outcome_class": outcome_class, "evidence": fixture.get("evidence")},
    )

    result = classifier.classify(status, contract)
    assert result.outcome_class == outcome_class
    if outcome_class in {"confirmed", "declined"}:
        assert result.stop_condition_met is True
    else:
        assert result.stop_condition_met is False


def test_classifier_demotes_low_confidence_to_ambiguous(classifier, contract):
    status = CallStatus(
        run_id="run_low_conf",
        status="completed",
        structured_result={"decision": "confirmed"},
        raw_data={
            "outcome_class": "confirmed",
            "evidence": {"confidence": 0.35, "excerpt": "murmur..."},
        },
    )
    result = classifier.classify(status, contract)
    assert result.outcome_class == "ambiguous"
    assert result.stop_condition_met is False


def test_classifier_detects_error_status(classifier, contract):
    status = CallStatus(
        run_id="run_error",
        status="failed",
        error="SIP_504_TIMEOUT",
        structured_result={},
    )
    result = classifier.classify(status, contract)
    assert result.outcome_class == "error"
    assert result.stop_condition_met is False


def test_classifier_schema_validation_failure(classifier):
    # Contract requires integer code, but structured result has string
    strict_contract = OutcomeContract(
        name="strict_schema",
        result_schema={
            "type": "object",
            "required": ["code"],
            "properties": {"code": {"type": "integer"}},
        },
        stop_when="code == 200",
    )
    status = CallStatus(
        run_id="run_invalid_schema",
        status="completed",
        structured_result={"code": "200_string_not_int"},  # Violation!
    )
    result = classifier.classify(status, strict_contract)
    assert result.result_validation["valid"] is False
    assert len(result.result_validation["errors"]) > 0
    assert result.stop_condition_met is False
