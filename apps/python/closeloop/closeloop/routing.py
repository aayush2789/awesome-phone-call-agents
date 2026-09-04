"""CloseLoop Data-Driven Routing Engine.

Evaluates normalized OutcomeResults against declarative Policy configurations
to select the next orchestration action independently of telephony providers.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from closeloop.models import ContactRung, OutcomeResult, Policy


class RoutingAction(str, Enum):
    """Next action selected by the declarative routing engine."""

    CLOSE = "CLOSE"
    RETRY = "RETRY"
    NEXT_RUNG = "NEXT_RUNG"
    SCHEDULE = "SCHEDULE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    SUPPRESS_AND_STOP = "SUPPRESS_AND_STOP"
    FAIL_CLOSED = "FAIL_CLOSED"
    TERMINATE = "TERMINATE"


class RoutingDecision(BaseModel):
    """Actionable decision returned by the routing engine."""

    model_config = ConfigDict(extra="forbid")

    action: RoutingAction = Field(..., description="Selected routing directive")
    reason: str = Field(..., description="Actionable rationale for this routing choice")
    target_rung: Optional[str] = Field(default=None, description="Target rung if advancing or transferring")
    schedule_delay_seconds: Optional[int] = Field(default=None, description="Delay if scheduling future attempt")
    suppress_phone: Optional[str] = Field(default=None, description="Phone number to suppress if hard refusal occurred")


class RoutingEngine:
    """Evaluates outcomes and policy rules to determine the next workflow step."""

    def route(
        self,
        outcome: OutcomeResult,
        current_rung: ContactRung,
        policy: Policy,
        attempts_on_rung: int,
        total_calls_placed: int,
        next_rung: Optional[ContactRung] = None,
    ) -> RoutingDecision:
        """Select next orchestration action based on outcome and policy."""
        # 1. Successful Outcome Closure
        if outcome.stop_condition_met:
            return RoutingDecision(
                action=RoutingAction.CLOSE,
                reason=f"Outcome stop condition satisfied on rung '{current_rung.rung}'.",
                target_rung=current_rung.rung,
            )

        # 2. Hard Refusal / Explicit Opt-Out
        if outcome.outcome_class == "hard_refusal" or outcome.decision == "hard_refusal":
            return RoutingDecision(
                action=RoutingAction.SUPPRESS_AND_STOP,
                reason="Callee issued an explicit hard refusal/do-not-call directive. Permanent stop.",
                suppress_phone=current_rung.phone,
            )

        # 3. Budget Limits
        if total_calls_placed >= policy.max_calls_total:
            return RoutingDecision(
                action=RoutingAction.TERMINATE,
                reason=f"Global call budget exhausted ({total_calls_placed} >= {policy.max_calls_total}).",
            )

        # 4. Voicemail
        if outcome.outcome_class == "voicemail":
            if policy.on_voicemail == "retry" and attempts_on_rung < current_rung.max_attempts:
                return RoutingDecision(
                    action=RoutingAction.RETRY,
                    reason=f"Voicemail detected; policy specifies retry on same rung ({attempts_on_rung}/{current_rung.max_attempts}).",
                    target_rung=current_rung.rung,
                )
            elif next_rung:
                return RoutingDecision(
                    action=RoutingAction.NEXT_RUNG,
                    reason=f"Voicemail detected; escalating from '{current_rung.rung}' to '{next_rung.rung}'.",
                    target_rung=next_rung.rung,
                )
            else:
                return RoutingDecision(
                    action=RoutingAction.TERMINATE,
                    reason=f"Voicemail detected on final rung '{current_rung.rung}'. Ladder exhausted.",
                )

        # 5. Wrong Person
        if outcome.outcome_class == "wrong_person":
            if next_rung:
                return RoutingDecision(
                    action=RoutingAction.NEXT_RUNG,
                    reason=f"Recipient mismatch on '{current_rung.rung}'. Escalating to alternate '{next_rung.rung}'.",
                    target_rung=next_rung.rung,
                )
            else:
                return RoutingDecision(
                    action=RoutingAction.HUMAN_REVIEW,
                    reason="Wrong person reached and no further rungs remain. Coordinator review required.",
                )

        # 6. Callback Requested
        if outcome.outcome_class == "callback_requested":
            if policy.on_callback_requested == "schedule_retry":
                return RoutingDecision(
                    action=RoutingAction.SCHEDULE,
                    reason="Callee requested a callback. Scheduling deferred contact.",
                    target_rung=current_rung.rung,
                    schedule_delay_seconds=1800,  # 30-minute default deferral
                )
            elif next_rung:
                return RoutingDecision(
                    action=RoutingAction.NEXT_RUNG,
                    reason="Callback requested; policy dictates advancing to next rung.",
                    target_rung=next_rung.rung,
                )

        # 7. Screening / Gatekeeper
        if outcome.outcome_class == "screening":
            if attempts_on_rung < current_rung.max_attempts:
                return RoutingDecision(
                    action=RoutingAction.RETRY,
                    reason=f"Gatekeeper screening encountered; retrying attempt ({attempts_on_rung}/{current_rung.max_attempts}).",
                    target_rung=current_rung.rung,
                )
            elif next_rung:
                return RoutingDecision(
                    action=RoutingAction.NEXT_RUNG,
                    reason="Gatekeeper screening exhausted on current rung. Escalating.",
                    target_rung=next_rung.rung,
                )
            else:
                return RoutingDecision(
                    action=RoutingAction.HUMAN_REVIEW,
                    reason="Screening gatekeeper blocked all contact attempts. Flagged for human review.",
                )

        # 8. Ambiguous Result
        if outcome.outcome_class == "ambiguous":
            if attempts_on_rung < current_rung.max_attempts:
                return RoutingDecision(
                    action=RoutingAction.RETRY,
                    reason="Ambiguous response received; retrying clarification attempt.",
                    target_rung=current_rung.rung,
                )
            else:
                return RoutingDecision(
                    action=RoutingAction.HUMAN_REVIEW,
                    reason="Outcome remained ambiguous after attempt limits. Flagged for human review.",
                )

        # 9. Provider / Transport Errors
        if outcome.outcome_class == "error":
            if policy.on_error == "fail_closed":
                return RoutingDecision(
                    action=RoutingAction.FAIL_CLOSED,
                    reason="Telephony error encountered and policy is configured to fail-closed.",
                )
            elif next_rung:
                return RoutingDecision(
                    action=RoutingAction.NEXT_RUNG,
                    reason="Telephony error encountered on current rung; attempting alternate rung.",
                    target_rung=next_rung.rung,
                )

        # 10. Default (e.g. no_answer, partial)
        if attempts_on_rung < current_rung.max_attempts:
            return RoutingDecision(
                action=RoutingAction.RETRY,
                reason=f"Rung '{current_rung.rung}' did not reach terminal closure; attempt {attempts_on_rung}/{current_rung.max_attempts}.",
                target_rung=current_rung.rung,
            )
        elif next_rung:
            return RoutingDecision(
                action=RoutingAction.NEXT_RUNG,
                reason=f"Attempts exhausted on '{current_rung.rung}'. Advancing to '{next_rung.rung}'.",
                target_rung=next_rung.rung,
            )
        else:
            return RoutingDecision(
                action=RoutingAction.TERMINATE,
                reason="All ladder rungs and allowed attempts exhausted without outcome closure.",
            )
