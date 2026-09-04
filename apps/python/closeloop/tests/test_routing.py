"""Tests for CloseLoop Data-Driven Routing Engine (Phase 7)."""

import pytest
from closeloop.models import ContactRung, OutcomeResult, Policy
from closeloop.routing import RoutingAction, RoutingEngine


@pytest.fixture
def routing_engine():
    return RoutingEngine()


@pytest.fixture
def current_rung():
    return ContactRung(
        rung="candidate",
        phone="+15550101234",
        consent_basis="candidate portal registration",
        max_attempts=2,
    )


@pytest.fixture
def next_rung():
    return ContactRung(
        rung="alternate_number",
        phone="+15550101235",
        consent_basis="candidate alternate number",
        max_attempts=1,
    )


def test_routing_close_on_stop_condition_met(routing_engine, current_rung):
    policy = Policy()
    outcome = OutcomeResult(outcome_class="confirmed", stop_condition_met=True)
    decision = routing_engine.route(outcome, current_rung, policy, attempts_on_rung=1, total_calls_placed=1)

    assert decision.action == RoutingAction.CLOSE
    assert decision.target_rung == "candidate"


def test_routing_hard_refusal_suppresses_and_stops(routing_engine, current_rung, next_rung):
    policy = Policy(on_hard_refusal="stop_chain")
    outcome = OutcomeResult(outcome_class="hard_refusal", decision="hard_refusal", stop_condition_met=False)
    decision = routing_engine.route(
        outcome, current_rung, policy, attempts_on_rung=1, total_calls_placed=1, next_rung=next_rung
    )

    assert decision.action == RoutingAction.SUPPRESS_AND_STOP
    assert decision.suppress_phone == "+15550101234"


def test_routing_voicemail_escalates_to_next_rung(routing_engine, current_rung, next_rung):
    policy = Policy(on_voicemail="next_rung")
    outcome = OutcomeResult(outcome_class="voicemail", stop_condition_met=False)
    decision = routing_engine.route(
        outcome, current_rung, policy, attempts_on_rung=1, total_calls_placed=1, next_rung=next_rung
    )

    assert decision.action == RoutingAction.NEXT_RUNG
    assert decision.target_rung == "alternate_number"


def test_routing_voicemail_retries_if_configured(routing_engine, current_rung, next_rung):
    policy = Policy(on_voicemail="retry")
    outcome = OutcomeResult(outcome_class="voicemail", stop_condition_met=False)
    # 1 attempt completed out of 2 max_attempts
    decision = routing_engine.route(
        outcome, current_rung, policy, attempts_on_rung=1, total_calls_placed=1, next_rung=next_rung
    )

    assert decision.action == RoutingAction.RETRY
    assert decision.target_rung == "candidate"


def test_routing_wrong_person_escalates(routing_engine, current_rung, next_rung):
    policy = Policy(on_wrong_person="transfer_then_next")
    outcome = OutcomeResult(outcome_class="wrong_person", stop_condition_met=False)
    decision = routing_engine.route(
        outcome, current_rung, policy, attempts_on_rung=1, total_calls_placed=1, next_rung=next_rung
    )

    assert decision.action == RoutingAction.NEXT_RUNG
    assert decision.target_rung == "alternate_number"


def test_routing_budget_exhaustion(routing_engine, current_rung, next_rung):
    policy = Policy(max_calls_total=2)
    outcome = OutcomeResult(outcome_class="no_answer", stop_condition_met=False)
    # Already placed 2 calls, at budget limit
    decision = routing_engine.route(
        outcome, current_rung, policy, attempts_on_rung=1, total_calls_placed=2, next_rung=next_rung
    )

    assert decision.action == RoutingAction.TERMINATE
    assert "budget exhausted" in decision.reason.lower()


def test_routing_screening_flags_human_review_when_exhausted(routing_engine, current_rung):
    policy = Policy()
    outcome = OutcomeResult(outcome_class="screening", stop_condition_met=False)
    # 2 attempts of 2 exhausted and no next rung
    decision = routing_engine.route(
        outcome, current_rung, policy, attempts_on_rung=2, total_calls_placed=2, next_rung=None
    )

    assert decision.action == RoutingAction.HUMAN_REVIEW
