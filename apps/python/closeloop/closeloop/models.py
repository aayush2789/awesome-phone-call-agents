"""CloseLoop Typed Domain Models.

Provider-independent data models for outcome-driven phone-call workflows.
Enforces type safety, JSON schema adherence, idempotency tracking,
and integration with CloseLoop safety invariants.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from closeloop.safety import (
    ConsentMissingError,
    E164ValidationError,
    is_in_quiet_hours,
    mask_phone,
    validate_consent_basis,
    validate_e164,
)


def compute_idempotency_key(run_id: str, rung: str, attempt: int) -> str:
    """Compute deterministic idempotency key sha256(run_id:rung:attempt)."""
    payload = f"{run_id.strip()}:{rung.strip()}:{attempt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class QuietHoursConfig(BaseModel):
    """Quiet hours window definition in a specific timezone."""

    model_config = ConfigDict(extra="forbid")

    start: str = Field(..., description="Start of quiet hours in HH:MM format, e.g. '21:00'")
    end: str = Field(..., description="End of quiet hours in HH:MM format, e.g. '09:00'")
    timezone: str = Field(..., description="IANA timezone name, e.g. 'Asia/Kolkata'")

    def is_active(self, current_dt: datetime | None = None) -> bool:
        """Evaluate if the current time falls inside quiet hours."""
        return is_in_quiet_hours(self.start, self.end, self.timezone, current_dt=current_dt)


class OutcomeContract(BaseModel):
    """Specification of desired business outcome and verification rules."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1, description="Unique outcome name, e.g. 'interview_slot_confirmation'")
    deadline: Optional[str | datetime] = Field(default=None, description="ISO-8601 deadline for closing the outcome")
    quiet_hours: Optional[QuietHoursConfig] = Field(default=None, description="Quiet hours configuration")
    result_schema: dict[str, Any] = Field(default_factory=dict, description="JSON Schema defining valid structured results")
    stop_when: Optional[str | dict[str, Any]] = Field(
        default=None,
        description="Stop condition rule or expression (e.g. 'decision in [confirmed, declined]')",
    )


class ContactRung(BaseModel):
    """A single contact tier or participant in an escalation ladder."""

    model_config = ConfigDict(extra="allow")

    rung: str = Field(..., min_length=1, description="Identifier for this rung (e.g. 'candidate', 'mentor')")
    phone: str = Field(..., description="Target phone number strictly in ITU-T E.164 format")
    region: Optional[str] = Field(default=None, description="Two-letter region/country code (e.g. 'IN', 'US')")
    language: str = Field(default="English", description="Target conversational language")
    consent_basis: str = Field(..., description="Explicit, documented legal/operational consent basis")
    max_attempts: int = Field(default=1, ge=1, description="Maximum calls allowed on this rung")

    @field_validator("phone")
    @classmethod
    def validate_phone_e164(cls, v: str) -> str:
        if not validate_e164(v):
            raise E164ValidationError(f"Phone number '{v}' is not a valid ITU-T E.164 format.")
        return v.strip()

    @field_validator("consent_basis")
    @classmethod
    def validate_consent(cls, v: str) -> str:
        if not validate_consent_basis(v):
            raise ConsentMissingError(
                f"Consent basis '{v}' is insufficient or missing. Explicit documented consent is required."
            )
        return v.strip()


class Policy(BaseModel):
    """Orchestration policy parameters and fallback routing rules."""

    model_config = ConfigDict(extra="allow")

    max_calls_total: int = Field(default=3, ge=1, description="Total call budget across all rungs")
    on_voicemail: str = Field(default="next_rung", description="Action when voicemail is detected")
    on_callback_requested: str = Field(default="schedule_retry", description="Action when callee requests callback")
    on_wrong_person: str = Field(default="transfer_then_next", description="Action when wrong person is reached")
    on_hard_refusal: str = Field(default="stop_chain", description="Action when callee explicitly refuses/opts-out")
    on_no_answer: str = Field(default="retry_then_next", description="Action on unanswered call")
    on_error: str = Field(default="fail_closed", description="Action on provider or transport failure")


class StrategyConfig(BaseModel):
    """Strategy configuration for ladder execution."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(default="cascade", description="Strategy type: cascade, quorum, etc.")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Strategy-specific parameters")


class WritebackConfig(BaseModel):
    """Result writeback target configuration."""

    model_config = ConfigDict(extra="allow")

    target: str = Field(default="csv", description="Writeback destination type (csv, json, sqlite, webhook)")
    path: Optional[str] = Field(default=None, description="Local file path for file writeback")
    url: Optional[str] = Field(default=None, description="Endpoint URL for webhook writeback")


class WorkflowSpec(BaseModel):
    """Root declarative specification for an outcome-closing workflow."""

    model_config = ConfigDict(extra="allow")

    run_id: str = Field(..., min_length=1, description="Unique identifier for this workflow execution")
    outcome: OutcomeContract = Field(..., description="Target outcome specification")
    strategy: StrategyConfig = Field(default_factory=lambda: StrategyConfig(type="cascade"))
    ladder: list[ContactRung] = Field(default_factory=list, description="Ordered contact rungs")
    policy: Policy = Field(default_factory=Policy, description="Budget and routing policy")
    writeback: Optional[WritebackConfig] = Field(default=None, description="Optional result export target")

    @classmethod
    def from_yaml(cls, yaml_str: str) -> WorkflowSpec:
        """Parse a WorkflowSpec from a YAML string."""
        data = yaml.safe_load(yaml_str)
        if not isinstance(data, dict):
            raise ValueError("YAML content must be a dictionary mapping.")
        return cls(**data)

    @classmethod
    def from_yaml_file(cls, path: str) -> WorkflowSpec:
        """Parse a WorkflowSpec from a YAML file."""
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_yaml(f.read())


class CallPlan(BaseModel):
    """Proposed call configuration requiring mandatory safety inspection before dialing."""

    model_config = ConfigDict(extra="allow")

    plan_id: str = Field(..., description="Unique plan identifier")
    rung: str = Field(..., description="Target rung name")
    phone: str = Field(..., description="Target recipient E.164 phone number")
    goal: str = Field(..., description="Rendered goal for the voice agent")
    script: Optional[str] = Field(default=None, description="Optional conversation script or instructions")
    language: str = Field(default="English", description="Spoken language")
    recipient_role: Optional[str] = Field(default=None, description="Role of the recipient")
    approved: bool = Field(default=False, description="Whether human/policy inspection has approved this plan")
    rejection_reason: Optional[str] = Field(default=None, description="Reason if the plan was rejected")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_plan: dict[str, Any] = Field(default_factory=dict, description="Raw provider plan representation")

    def approve(self) -> None:
        """Approve the plan after inspection."""
        self.approved = True
        self.rejection_reason = None

    def reject(self, reason: str) -> None:
        """Reject the plan during inspection."""
        self.approved = False
        self.rejection_reason = reason


class CallRun(BaseModel):
    """Runtime execution record of an active or finished call."""

    model_config = ConfigDict(extra="allow")

    run_id: str = Field(..., description="CloseLoop execution attempt ID or run identifier")
    external_call_id: Optional[str] = Field(default=None, description="Telephony provider call identifier")
    plan_id: Optional[str] = Field(default=None, description="Associated CallPlan identifier")
    status: str = Field(default="planned", description="Call status: planned, running, completed, failed, cancelled, timed_out")
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    structured_result: Optional[dict[str, Any]] = Field(default=None, description="Extracted result from the call")
    raw_output: dict[str, Any] = Field(default_factory=dict, description="Raw provider output payload")
    error: Optional[str] = None


class Evidence(BaseModel):
    """Structured provenance supporting an outcome decision."""

    model_config = ConfigDict(extra="allow")

    source: str = Field(default="transcript", description="Evidence source: transcript, dtmf, provider, callee")
    speaker: Optional[str] = Field(default=None, description="Speaker identifier: callee, agent, ivr")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    excerpt: Optional[str] = Field(default=None, description="Direct supporting text excerpt")
    excerpt_hash: Optional[str] = Field(default=None, description="SHA-256 hash of the supporting excerpt")
    rationale: Optional[str] = Field(default=None, description="Explanation of how the evidence supports the outcome")

    @model_validator(mode="after")
    def compute_hash_if_missing(self) -> Evidence:
        if self.excerpt and not self.excerpt_hash:
            self.excerpt_hash = hashlib.sha256(self.excerpt.encode("utf-8")).hexdigest()
        return self


class OutcomeResult(BaseModel):
    """Interpreted outcome from a call attempt against the outcome contract."""

    model_config = ConfigDict(extra="allow")

    outcome_class: str = Field(
        ...,
        description="Outcome category: confirmed, reschedule, declined, unreachable, voicemail, callback_requested, wrong_person, hard_refusal, error",
    )
    decision: Optional[str] = Field(default=None, description="Extracted decision key matching contract schema")
    structured_result: dict[str, Any] = Field(default_factory=dict, description="Extracted key-value results")
    result_validation: dict[str, Any] = Field(
        default_factory=lambda: {"valid": True},
        description="Schema validation report: {'valid': bool, 'errors': list[str]}",
    )
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    evidence: Optional[Evidence] = Field(default=None, description="Structured provenance")
    stop_condition_met: bool = Field(default=False, description="True if this outcome satisfied the workflow stop rule")
    raw_result: dict[str, Any] = Field(default_factory=dict)


class AuditEntry(BaseModel):
    """Structured audit trail record tracking state changes and actions."""

    model_config = ConfigDict(extra="allow")

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    rung: str = Field(..., description="Target rung at this audit step")
    attempt: int = Field(..., ge=1, description="Attempt number on this rung")
    action: str = Field(..., description="Action performed: planned, approved, call_started, evaluated, escalated, closed, suppressed, failed")
    outcome_class: Optional[str] = Field(default=None, description="Interpreted outcome class if available")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional context or redacted metadata")


class CallAttempt(BaseModel):
    """Idempotent unit of call execution within a workflow ladder."""

    model_config = ConfigDict(extra="allow")

    idempotency_key: str = Field(default="", description="Deterministic SHA-256 key: run_id:rung:attempt")
    run_id: str = Field(..., description="Workflow run ID")
    rung: str = Field(..., description="Rung name")
    attempt: int = Field(..., ge=1, description="Attempt index on this rung")
    phone: str = Field(..., description="Recipient E.164 phone number")
    status: str = Field(default="planned", description="Lifecycle state: planned, executing, terminal, cancelled")
    plan: Optional[CallPlan] = None
    run: Optional[CallRun] = None
    outcome: Optional[OutcomeResult] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error: Optional[str] = None

    @model_validator(mode="after")
    def populate_idempotency_key(self) -> CallAttempt:
        if not self.idempotency_key:
            self.idempotency_key = compute_idempotency_key(self.run_id, self.rung, self.attempt)
        return self


class ExecutionLedger(BaseModel):
    """In-memory and persistent tracking ledger preventing duplicate calls."""

    model_config = ConfigDict(extra="allow")

    run_id: str = Field(..., description="Workflow run ID")
    attempts: list[CallAttempt] = Field(default_factory=list, description="Recorded attempts")
    total_calls_placed: int = Field(default=0, ge=0)
    total_calls_avoided: int = Field(default=0, ge=0)
    status: str = Field(default="active", description="Workflow state: active, completed, blocked, cancelled")

    def get_attempt(self, key: str) -> Optional[CallAttempt]:
        """Find an attempt by idempotency key."""
        for att in self.attempts:
            if att.idempotency_key == key:
                return att
        return None

    def record_attempt(self, attempt: CallAttempt) -> None:
        """Record or update an attempt in the ledger."""
        for idx, existing in enumerate(self.attempts):
            if existing.idempotency_key == attempt.idempotency_key:
                self.attempts[idx] = attempt
                return
        self.attempts.append(attempt)

    def is_attempt_terminal(self, rung: str, attempt_index: int) -> bool:
        """Check whether a specific rung attempt has already reached a terminal state."""
        key = compute_idempotency_key(self.run_id, rung, attempt_index)
        att = self.get_attempt(key)
        return att is not None and att.status == "terminal"


class WorkflowResult(BaseModel):
    """Standardized result envelope aligned with CloseLoop Section 19."""

    model_config = ConfigDict(extra="allow")

    run_id: str
    status: str = Field(..., description="Terminal state: closed, not_closed, blocked, human_review, terminated")
    outcome: str = Field(..., description="Interpreted outcome class, e.g. confirmed, declined, unreachable")
    summary: str = Field(..., description="Human-readable executive summary")
    structured_result: Optional[dict[str, Any]] = None
    result_validation: Optional[dict[str, Any]] = None
    closed_on_rung: Optional[str] = None
    attempt_index: Optional[int] = None
    calls_placed: int = 0
    calls_avoided: int = 0
    external_call_id: Optional[str] = None
    recipient_phone_e164: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    source_platform: str = "closeloop"
    source_object_id: Optional[str] = None
    transcript_url: Optional[str] = None
    recording_url: Optional[str] = None
    audit: list[AuditEntry] = Field(default_factory=list)

    def to_envelope_dict(self) -> dict[str, Any]:
        """Convert into standard public envelope format with phone masking and ISO dates."""
        audit_payload = [
            {
                "rung": entry.rung,
                "attempt": entry.attempt,
                "action": entry.action,
                "outcome_class": entry.outcome_class,
                "timestamp": entry.timestamp.isoformat(),
            }
            for entry in self.audit
        ]

        masked_recipient = (
            mask_phone(self.recipient_phone_e164) if self.recipient_phone_e164 else None
        )

        return {
            "run_id": self.run_id,
            "status": self.status,
            "outcome": self.outcome,
            "summary": self.summary,
            "structured_result": self.structured_result,
            "result_validation": self.result_validation or {"valid": True},
            "closed_on_rung": self.closed_on_rung,
            "attempt_index": self.attempt_index,
            "calls_placed": self.calls_placed,
            "calls_avoided": self.calls_avoided,
            "external_call_id": self.external_call_id,
            "recipient_phone_e164": masked_recipient,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "source_platform": self.source_platform,
            "source_object_id": self.source_object_id,
            "transcript_url": self.transcript_url,
            "recording_url": self.recording_url,
            "audit": audit_payload,
        }
