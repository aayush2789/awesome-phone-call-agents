"""CloseLoop Outcome Classifier.

Converts raw telephony and provider status payloads into normalized,
typed CloseLoop outcome classes using deterministic rules and schema validation.
"""

from __future__ import annotations

from typing import Any, Optional
import jsonschema

from closeloop.adapter import ALL_OUTCOME_CLASSES, CallStatus
from closeloop.models import Evidence, OutcomeContract, OutcomeResult

MIN_CONFIDENCE_THRESHOLD = 0.50


class OutcomeClassifier:
    """Deterministic rule-based outcome classifier for CloseLoop call results."""

    def classify(
        self,
        call_status: CallStatus,
        contract: Optional[OutcomeContract] = None,
    ) -> OutcomeResult:
        """Classify raw call output into a normalized typed OutcomeResult."""
        raw_structured: dict[str, Any] = call_status.structured_result or {}
        raw_fixture: dict[str, Any] = call_status.raw_data or {}
        evidence_dict = raw_fixture.get("evidence") or {}

        # 1. Check for Transport / Provider Errors
        if call_status.status == "failed" or call_status.error:
            return self._build_result(
                outcome_class="error",
                decision=None,
                structured_result=raw_structured,
                evidence=self._parse_evidence(evidence_dict, default_source="telephony", default_speaker="provider"),
                raw_result=raw_fixture,
                contract=contract,
            )

        # 2. Extract Fixture / Metadata Hints
        hinted_class = raw_fixture.get("outcome_class")
        if hinted_class and hinted_class in ALL_OUTCOME_CLASSES:
            evidence = self._parse_evidence(evidence_dict)
            # If evidence confidence is explicitly provided and too low, demote to ambiguous
            if evidence and evidence.confidence is not None and evidence.confidence < MIN_CONFIDENCE_THRESHOLD:
                return self._build_result(
                    outcome_class="ambiguous",
                    decision="unknown",
                    structured_result=raw_structured,
                    evidence=evidence,
                    raw_result=raw_fixture,
                    contract=contract,
                )

            decision = raw_structured.get("decision")
            return self._build_result(
                outcome_class=hinted_class,
                decision=decision,
                structured_result=raw_structured,
                evidence=evidence,
                raw_result=raw_fixture,
                contract=contract,
            )

        # 3. Deterministic Signal Analysis
        call_status_key = raw_structured.get("call_status", "").lower()
        decision_key = str(raw_structured.get("decision", "")).lower()

        # Telephony level signals
        if call_status_key == "no_answer" or raw_structured.get("answered") is False:
            return self._build_result(
                outcome_class="no_answer",
                decision=None,
                structured_result=raw_structured,
                evidence=self._parse_evidence(evidence_dict, default_source="telephony", default_speaker="ivr"),
                raw_result=raw_fixture,
                contract=contract,
            )

        if call_status_key == "voicemail" or raw_structured.get("beep_detected") is True:
            return self._build_result(
                outcome_class="voicemail",
                decision=None,
                structured_result=raw_structured,
                evidence=self._parse_evidence(evidence_dict, default_source="transcript", default_speaker="ivr"),
                raw_result=raw_fixture,
                contract=contract,
            )

        if call_status_key == "screening" or "gatekeeper_action" in raw_structured:
            return self._build_result(
                outcome_class="screening",
                decision=None,
                structured_result=raw_structured,
                evidence=self._parse_evidence(evidence_dict, default_source="transcript", default_speaker="callee"),
                raw_result=raw_fixture,
                contract=contract,
            )

        if call_status_key == "wrong_person" or "callee_clarification" in raw_structured:
            return self._build_result(
                outcome_class="wrong_person",
                decision=None,
                structured_result=raw_structured,
                evidence=self._parse_evidence(evidence_dict, default_source="transcript", default_speaker="callee"),
                raw_result=raw_fixture,
                contract=contract,
            )

        # Semantic decision signals
        if decision_key in {"hard_refusal", "refused", "opt_out", "dnc"} or raw_structured.get("dnc") is True:
            return self._build_result(
                outcome_class="hard_refusal",
                decision="hard_refusal",
                structured_result=raw_structured,
                evidence=self._parse_evidence(evidence_dict, default_source="transcript", default_speaker="callee"),
                raw_result=raw_fixture,
                contract=contract,
            )

        if decision_key in {"callback_requested", "callback", "call_later"} or "callback_time" in raw_structured:
            return self._build_result(
                outcome_class="callback_requested",
                decision="callback_requested",
                structured_result=raw_structured,
                evidence=self._parse_evidence(evidence_dict, default_source="transcript", default_speaker="callee"),
                raw_result=raw_fixture,
                contract=contract,
            )

        if decision_key in {"confirmed", "confirm", "accepted", "yes"}:
            return self._build_result(
                outcome_class="confirmed",
                decision="confirmed",
                structured_result=raw_structured,
                evidence=self._parse_evidence(evidence_dict, default_source="transcript", default_speaker="callee"),
                raw_result=raw_fixture,
                contract=contract,
            )

        if decision_key in {"reschedule", "change_slot", "different_time"}:
            return self._build_result(
                outcome_class="reschedule",
                decision="reschedule",
                structured_result=raw_structured,
                evidence=self._parse_evidence(evidence_dict, default_source="transcript", default_speaker="callee"),
                raw_result=raw_fixture,
                contract=contract,
            )

        if decision_key in {"declined", "decline", "withdraw", "no"}:
            return self._build_result(
                outcome_class="declined",
                decision="declined",
                structured_result=raw_structured,
                evidence=self._parse_evidence(evidence_dict, default_source="transcript", default_speaker="callee"),
                raw_result=raw_fixture,
                contract=contract,
            )

        # 4. Fallback for Ambiguous / Unrecognized Results
        return self._build_result(
            outcome_class="ambiguous",
            decision=raw_structured.get("decision") or "unknown",
            structured_result=raw_structured,
            evidence=self._parse_evidence(evidence_dict, default_source="transcript", default_speaker="callee"),
            raw_result=raw_fixture,
            contract=contract,
        )

    def _parse_evidence(
        self,
        data: dict[str, Any],
        default_source: str = "transcript",
        default_speaker: str = "callee",
    ) -> Optional[Evidence]:
        """Construct Evidence model from dictionary data."""
        if not data:
            return None
        return Evidence(
            source=data.get("source", default_source),
            speaker=data.get("speaker", default_speaker),
            confidence=data.get("confidence"),
            excerpt=data.get("excerpt"),
            excerpt_hash=data.get("excerpt_hash"),
            rationale=data.get("rationale"),
        )

    def _build_result(
        self,
        outcome_class: str,
        decision: Optional[str],
        structured_result: dict[str, Any],
        evidence: Optional[Evidence],
        raw_result: dict[str, Any],
        contract: Optional[OutcomeContract],
    ) -> OutcomeResult:
        """Validate against contract schema and construct OutcomeResult."""
        validation_errors: list[str] = []
        schema_valid = True

        if contract and contract.result_schema and structured_result:
            try:
                jsonschema.validate(instance=structured_result, schema=contract.result_schema)
            except jsonschema.ValidationError as val_err:
                schema_valid = False
                validation_errors.append(val_err.message)
            except Exception as err:
                schema_valid = False
                validation_errors.append(str(err))

        stop_met = False
        if (
            outcome_class not in {"ambiguous", "error"}
            and schema_valid
            and contract
            and contract.stop_when
        ):
            from closeloop.engine import evaluate_stop_condition

            stop_met = evaluate_stop_condition(contract.stop_when, structured_result)

        confidence = evidence.confidence if evidence else None

        return OutcomeResult(
            outcome_class=outcome_class,
            decision=decision,
            structured_result=structured_result,
            result_validation={"valid": schema_valid, "errors": validation_errors},
            confidence=confidence,
            evidence=evidence,
            stop_condition_met=stop_met,
            raw_result=raw_result,
        )
