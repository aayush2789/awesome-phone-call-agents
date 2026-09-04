"""CloseLoop Standalone Workflow Specification Validator.

Validates declarative workflow specifications before execution, rejecting
invalid configurations fail-closed with machine-readable, path-indexed reports.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import jsonschema
import yaml
from pydantic import BaseModel, ConfigDict, Field

from closeloop.models import WorkflowSpec
from closeloop.safety import (
    parse_time_str,
    validate_consent_basis,
    validate_e164,
)

RUN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
VALID_STRATEGY_TYPES = {"cascade", "quorum", "broadcast"}
VALID_POLICY_ACTIONS = {
    "next_rung",
    "schedule_retry",
    "retry",
    "retry_then_next",
    "transfer_then_next",
    "stop_chain",
    "fail_closed",
    "escalate",
    "human_review",
}
VALID_WRITEBACK_TARGETS = {"csv", "json", "sqlite", "webhook"}


class ValidationErrorDetail(BaseModel):
    """Detailed record of a single specification validation violation."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="Path to offending field, e.g. 'ladder[1].consent_basis'")
    message: str = Field(..., description="Actionable explanation of the error")
    code: str = Field(..., description="Machine-readable error identifier")


class ValidationReport(BaseModel):
    """Machine-readable validation outcome report matching Section 33."""

    model_config = ConfigDict(extra="allow")

    ok: bool = Field(..., description="True if no validation errors were encountered")
    errors: list[ValidationErrorDetail] = Field(default_factory=list, description="List of validation errors")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal warnings or advisories")
    value: Optional[dict[str, Any]] = Field(default=None, description="Parsed specification dictionary if valid")

    def to_dict(self) -> dict[str, Any]:
        """Convert into canonical Section 33 machine-readable JSON structure."""
        if self.ok:
            payload: dict[str, Any] = {
                "ok": True,
                "value": self.value or {},
            }
            if self.warnings:
                payload["warnings"] = self.warnings
            return payload
        else:
            return {
                "ok": False,
                "errors": [
                    {
                        "path": err.path,
                        "message": err.message,
                        "code": err.code,
                    }
                    for err in self.errors
                ],
                "warnings": self.warnings,
            }


class WorkflowValidator:
    """Standalone validator for CloseLoop workflow specifications."""

    def __init__(self) -> None:
        self.errors: list[ValidationErrorDetail] = []
        self.warnings: list[str] = []

    def _add_error(self, path: str, message: str, code: str) -> None:
        self.errors.append(ValidationErrorDetail(path=path, message=message, code=code))

    def _add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def validate(self, raw_input: str | dict[str, Any] | Path | WorkflowSpec) -> ValidationReport:
        """Validate raw specification and produce a machine-readable ValidationReport."""
        self.errors = []
        self.warnings = []

        data: dict[str, Any]
        if isinstance(raw_input, WorkflowSpec):
            data = raw_input.model_dump()
        elif isinstance(raw_input, Path):
            if not raw_input.is_file():
                self._add_error("file", f"Specification file not found: {raw_input}", "FILE_NOT_FOUND")
                return ValidationReport(ok=False, errors=self.errors, warnings=self.warnings, value=None)
            try:
                with open(raw_input, "r", encoding="utf-8") as f:
                    parsed = yaml.safe_load(f.read())
            except Exception as err:
                self._add_error("syntax", f"YAML parsing failed: {err}", "SYNTAX_ERROR")
                return ValidationReport(ok=False, errors=self.errors, warnings=self.warnings, value=None)
            if not isinstance(parsed, dict):
                self._add_error("root", "YAML root must be a mapping", "ROOT_TYPE_ERROR")
                return ValidationReport(ok=False, errors=self.errors, warnings=self.warnings, value=None)
            data = parsed
        elif isinstance(raw_input, str):
            try:
                parsed = yaml.safe_load(raw_input)
            except Exception as err:
                self._add_error("syntax", f"YAML parsing failed: {err}", "SYNTAX_ERROR")
                return ValidationReport(ok=False, errors=self.errors, warnings=self.warnings, value=None)
            if not isinstance(parsed, dict):
                self._add_error("root", "YAML root must be a mapping", "ROOT_TYPE_ERROR")
                return ValidationReport(ok=False, errors=self.errors, warnings=self.warnings, value=None)
            data = parsed
        elif isinstance(raw_input, dict):
            data = raw_input
        else:
            self._add_error("root", f"Unsupported input type: {type(raw_input).__name__}", "INVALID_INPUT_TYPE")
            return ValidationReport(ok=False, errors=self.errors, warnings=self.warnings, value=None)

        # 1. Validate run_id
        run_id = data.get("run_id")
        if not run_id or not isinstance(run_id, str):
            self._add_error("run_id", "run_id is required and must be a non-empty string", "RUN_ID_REQUIRED")
        elif not RUN_ID_PATTERN.match(run_id.strip()):
            self._add_error(
                "run_id",
                f"run_id '{run_id}' must consist only of alphanumeric, hyphen, or underscore characters (max 128 chars)",
                "INVALID_RUN_ID_FORMAT",
            )

        # 2. Validate outcome
        outcome = data.get("outcome")
        if not outcome or not isinstance(outcome, dict):
            self._add_error("outcome", "outcome is required and must be an object", "OUTCOME_REQUIRED")
        else:
            self._validate_outcome(outcome)

        # 3. Validate strategy
        strategy = data.get("strategy")
        if strategy is not None:
            if not isinstance(strategy, dict):
                self._add_error("strategy", "strategy must be an object if specified", "STRATEGY_TYPE_ERROR")
            else:
                strat_type = strategy.get("type", "cascade")
                if strat_type not in VALID_STRATEGY_TYPES:
                    self._add_error(
                        "strategy.type",
                        f"Unsupported strategy type '{strat_type}'. Supported: {sorted(VALID_STRATEGY_TYPES)}",
                        "INVALID_STRATEGY_TYPE",
                    )

        # 4. Validate ladder
        ladder = data.get("ladder")
        if ladder is None:
            self._add_error("ladder", "ladder is required", "LADDER_REQUIRED")
        elif not isinstance(ladder, list) or len(ladder) == 0:
            self._add_error("ladder", "ladder must be a non-empty list of contact rungs", "EMPTY_LADDER")
        else:
            self._validate_ladder(ladder)

        # 5. Validate policy
        policy = data.get("policy")
        if policy is not None:
            if not isinstance(policy, dict):
                self._add_error("policy", "policy must be an object if specified", "POLICY_TYPE_ERROR")
            else:
                self._validate_policy(policy, ladder if isinstance(ladder, list) else [])

        # 6. Validate writeback
        writeback = data.get("writeback")
        if writeback is not None:
            if not isinstance(writeback, dict):
                self._add_error("writeback", "writeback must be an object if specified", "WRITEBACK_TYPE_ERROR")
            else:
                self._validate_writeback(writeback)

        # Build parsed WorkflowSpec if no errors
        valid_spec_value: Optional[dict[str, Any]] = None
        if not self.errors:
            try:
                spec_obj = WorkflowSpec(**data)
                valid_spec_value = spec_obj.model_dump()
            except Exception as err:
                self._add_error("pydantic", f"Model instantiation failed: {err}", "MODEL_ERROR")

        is_ok = len(self.errors) == 0
        return ValidationReport(
            ok=is_ok,
            errors=self.errors,
            warnings=self.warnings,
            value=valid_spec_value if is_ok else None,
        )

    def _validate_outcome(self, outcome: dict[str, Any]) -> None:
        """Validate outcome section."""
        name = outcome.get("name")
        if not name or not isinstance(name, str):
            self._add_error("outcome.name", "outcome.name is required and must be a non-empty string", "OUTCOME_NAME_REQUIRED")

        # Deadline
        deadline = outcome.get("deadline")
        if deadline is not None:
            if isinstance(deadline, str):
                try:
                    datetime.fromisoformat(deadline.replace("Z", "+00:00"))
                except ValueError:
                    self._add_error("outcome.deadline", f"Invalid ISO-8601 deadline format: '{deadline}'", "INVALID_DEADLINE")
            elif not isinstance(deadline, datetime):
                self._add_error("outcome.deadline", "outcome.deadline must be an ISO-8601 string or datetime", "INVALID_DEADLINE_TYPE")

        # Quiet hours
        quiet_hours = outcome.get("quiet_hours")
        if quiet_hours is not None:
            if not isinstance(quiet_hours, dict):
                self._add_error("outcome.quiet_hours", "quiet_hours must be an object", "QUIET_HOURS_TYPE_ERROR")
            else:
                self._validate_quiet_hours(quiet_hours)

        # Result schema
        schema = outcome.get("result_schema")
        if schema is not None:
            if not isinstance(schema, dict):
                self._add_error("outcome.result_schema", "result_schema must be a valid JSON schema dictionary", "SCHEMA_TYPE_ERROR")
            else:
                try:
                    jsonschema.Draft202012Validator.check_schema(schema)
                except jsonschema.SchemaError as err:
                    self._add_error("outcome.result_schema", f"Invalid JSON Schema: {err.message}", "INVALID_JSON_SCHEMA")
                except Exception as err:
                    self._add_error("outcome.result_schema", f"Schema validation failed: {err}", "SCHEMA_CHECK_ERROR")

        # Stop condition
        stop_when = outcome.get("stop_when")
        if stop_when is not None:
            if isinstance(stop_when, str):
                if not stop_when.strip():
                    self._add_error("outcome.stop_when", "stop_when expression cannot be empty", "EMPTY_STOP_WHEN")
            elif isinstance(stop_when, dict):
                if not stop_when:
                    self._add_error("outcome.stop_when", "stop_when object cannot be empty", "EMPTY_STOP_WHEN")
            else:
                self._add_error("outcome.stop_when", "stop_when must be a string expression or object rule", "STOP_WHEN_TYPE_ERROR")

    def _validate_quiet_hours(self, quiet_hours: dict[str, Any]) -> None:
        """Validate quiet hours window and timezone."""
        start = quiet_hours.get("start")
        if not start or not isinstance(start, str):
            self._add_error("outcome.quiet_hours.start", "quiet_hours.start is required and must be 'HH:MM'", "QUIET_START_REQUIRED")
        else:
            try:
                parse_time_str(start)
            except ValueError as err:
                self._add_error("outcome.quiet_hours.start", str(err), "INVALID_TIME_FORMAT")

        end = quiet_hours.get("end")
        if not end or not isinstance(end, str):
            self._add_error("outcome.quiet_hours.end", "quiet_hours.end is required and must be 'HH:MM'", "QUIET_END_REQUIRED")
        else:
            try:
                parse_time_str(end)
            except ValueError as err:
                self._add_error("outcome.quiet_hours.end", str(err), "INVALID_TIME_FORMAT")

        tz_str = quiet_hours.get("timezone")
        if not tz_str or not isinstance(tz_str, str):
            self._add_error("outcome.quiet_hours.timezone", "quiet_hours.timezone is required and must be an IANA timezone string", "TIMEZONE_REQUIRED")
        else:
            try:
                ZoneInfo(tz_str.strip())
            except ZoneInfoNotFoundError:
                self._add_error("outcome.quiet_hours.timezone", f"Unknown or invalid IANA timezone: '{tz_str}'", "INVALID_TIMEZONE")

    def _validate_ladder(self, ladder: list[Any]) -> None:
        """Validate ladder rungs."""
        seen_rungs: set[str] = set()

        for idx, rung_data in enumerate(ladder):
            path_prefix = f"ladder[{idx}]"
            if not isinstance(rung_data, dict):
                self._add_error(path_prefix, "Rung must be an object", "RUNG_TYPE_ERROR")
                continue

            rung_id = rung_data.get("rung")
            if not rung_id or not isinstance(rung_id, str):
                self._add_error(f"{path_prefix}.rung", "rung identifier is required and must be a non-empty string", "RUNG_ID_REQUIRED")
            else:
                rung_clean = rung_id.strip()
                if rung_clean in seen_rungs:
                    self._add_error(f"{path_prefix}.rung", f"Duplicate rung identifier '{rung_clean}' in ladder", "DUPLICATE_RUNG")
                seen_rungs.add(rung_clean)

            phone = rung_data.get("phone")
            if not phone or not isinstance(phone, str):
                self._add_error(f"{path_prefix}.phone", "phone is required and must be a valid E.164 number", "PHONE_REQUIRED")
            elif not validate_e164(phone):
                self._add_error(
                    f"{path_prefix}.phone",
                    f"Phone number '{phone}' is not a valid ITU-T E.164 number (must start with + and have 7-15 digits)",
                    "INVALID_PHONE_E164",
                )

            consent_basis = rung_data.get("consent_basis")
            if not consent_basis or not isinstance(consent_basis, str):
                self._add_error(f"{path_prefix}.consent_basis", "consent_basis is required", "CONSENT_REQUIRED")
            elif not validate_consent_basis(consent_basis):
                self._add_error(
                    f"{path_prefix}.consent_basis",
                    f"Consent basis '{consent_basis}' is insufficient. Must describe explicit legal/operational consent basis.",
                    "INSUFFICIENT_CONSENT",
                )

            max_attempts = rung_data.get("max_attempts", 1)
            if not isinstance(max_attempts, int) or max_attempts < 1:
                self._add_error(f"{path_prefix}.max_attempts", "max_attempts must be an integer >= 1", "INVALID_MAX_ATTEMPTS")

    def _validate_policy(self, policy: dict[str, Any], ladder: list[Any]) -> None:
        """Validate orchestration policy and budget limits."""
        max_calls = policy.get("max_calls_total")
        if max_calls is not None:
            if not isinstance(max_calls, int) or max_calls < 1:
                self._add_error("policy.max_calls_total", "policy.max_calls_total must be an integer >= 1", "INVALID_CALL_BUDGET")
            else:
                # Sum of max_attempts across rungs
                total_rung_attempts = sum(
                    r.get("max_attempts", 1) for r in ladder if isinstance(r, dict)
                )
                if total_rung_attempts > max_calls:
                    self._add_warning(
                        f"Sum of ladder max_attempts ({total_rung_attempts}) exceeds policy.max_calls_total ({max_calls}). "
                        "Workflow will stop when policy limit is reached."
                    )

        # Check action fields
        for field_name in [
            "on_voicemail",
            "on_callback_requested",
            "on_wrong_person",
            "on_hard_refusal",
            "on_no_answer",
            "on_error",
        ]:
            action = policy.get(field_name)
            if action is not None:
                if not isinstance(action, str):
                    self._add_error(f"policy.{field_name}", f"policy.{field_name} must be a string", "INVALID_ACTION_TYPE")
                elif action not in VALID_POLICY_ACTIONS:
                    self._add_error(
                        f"policy.{field_name}",
                        f"Unsupported policy action '{action}'. Supported: {sorted(VALID_POLICY_ACTIONS)}",
                        "INVALID_POLICY_ACTION",
                    )

    def _validate_writeback(self, writeback: dict[str, Any]) -> None:
        """Validate writeback export target."""
        target = writeback.get("target")
        if not target or not isinstance(target, str):
            self._add_error("writeback.target", "writeback.target is required", "WRITEBACK_TARGET_REQUIRED")
        elif target not in VALID_WRITEBACK_TARGETS:
            self._add_error(
                "writeback.target",
                f"Unsupported writeback target '{target}'. Supported: {sorted(VALID_WRITEBACK_TARGETS)}",
                "INVALID_WRITEBACK_TARGET",
            )
        else:
            if target in {"csv", "json", "sqlite"}:
                path = writeback.get("path")
                if not path or not isinstance(path, str):
                    self._add_error("writeback.path", f"writeback.path is required for target '{target}'", "WRITEBACK_PATH_REQUIRED")
            elif target == "webhook":
                url = writeback.get("url")
                if not url or not isinstance(url, str):
                    self._add_error("writeback.url", "writeback.url is required for target 'webhook'", "WRITEBACK_URL_REQUIRED")
                elif not (url.startswith("http://") or url.startswith("https://")):
                    self._add_error("writeback.url", "writeback.url must start with http:// or https://", "INVALID_WEBHOOK_URL")


def validate_workflow_spec(raw_input: str | dict[str, Any] | Path | WorkflowSpec) -> ValidationReport:
    """Convenience function to validate a CloseLoop workflow specification."""
    validator = WorkflowValidator()
    return validator.validate(raw_input)
