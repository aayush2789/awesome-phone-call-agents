"""CloseLoop CALL-E Adapter Interfaces and Deterministic FakeAdapter.

Provides the CalleAdapterBase abstraction and an in-memory FakeAdapter loaded
with deterministic fixtures for all 11 outcome classes. Enforces the
mandatory post-plan inspection gate before allowing execution.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field

from closeloop.models import CallPlan, CallRun
from closeloop.safety import (
    SafetyViolationError,
    check_domain_boundaries,
    mask_phone,
)

SUPPORTED_TOOLS = ["plan_call", "run_call", "get_call_run"]

ALL_OUTCOME_CLASSES = [
    "confirmed",
    "reschedule",
    "declined",
    "no_answer",
    "voicemail",
    "screening",
    "wrong_person",
    "callback_requested",
    "hard_refusal",
    "error",
    "ambiguous",
]


class AuthStatus(BaseModel):
    """CALL-E authentication status payload."""

    model_config = ConfigDict(extra="allow")

    authenticated: bool = Field(default=True, description="True if a valid auth token is available")
    user_id: Optional[str] = Field(default="user_hackathon_demo", description="Identifier of authenticated entity")
    expires_at: Optional[str] = Field(default="2029-01-01T00:00:00Z", description="Token expiration timestamp")
    cached: bool = Field(default=True, description="True if token is loaded from local credential cache")
    details: dict[str, Any] = Field(default_factory=dict)


class ToolsStatus(BaseModel):
    """CALL-E MCP tools availability status."""

    model_config = ConfigDict(extra="allow")

    available: bool = Field(default=True, description="True if all required MCP tools are present")
    tools: list[str] = Field(default_factory=lambda: list(SUPPORTED_TOOLS), description="List of detected tools")
    missing: list[str] = Field(default_factory=list, description="List of required tools that were missing")
    details: dict[str, Any] = Field(default_factory=dict)


class PlanRequest(BaseModel):
    """Parameters submitted to CALL-E to generate a pre-dial plan."""

    model_config = ConfigDict(extra="allow")

    run_id: str = Field(..., description="Workflow execution ID")
    rung: str = Field(..., description="Escalation ladder rung name")
    phone: str = Field(..., description="Target recipient E.164 phone number")
    goal: str = Field(..., description="Conversational goal for the call")
    script: Optional[str] = Field(default=None, description="Optional dialogue guidelines or prompt")
    language: str = Field(default="English", description="Target conversation language")
    recipient_role: Optional[str] = Field(default=None, description="Role of the recipient")
    context: dict[str, Any] = Field(default_factory=dict, description="Contextual variables for goal templating")


class PlanInspectionResult(BaseModel):
    """Result of the mandatory pre-dial safety inspection checkpoint."""

    model_config = ConfigDict(extra="allow")

    plan_id: str = Field(..., description="Identifier of the inspected CallPlan")
    approved: bool = Field(..., description="True if plan passed all safety inspection rules")
    reason: Optional[str] = Field(default=None, description="Explanation if inspection failed or was rejected")
    domain_violations: list[str] = Field(default_factory=list, description="List of prohibited domain boundaries detected")
    inspected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CallStatus(BaseModel):
    """Polling response payload representing active or terminal call status."""

    model_config = ConfigDict(extra="allow")

    run_id: str = Field(..., description="CloseLoop run attempt ID")
    external_call_id: Optional[str] = Field(default=None, description="Provider telephony call ID")
    status: str = Field(default="planned", description="Call state: planned, running, completed, failed, cancelled, timed_out")
    structured_result: Optional[dict[str, Any]] = Field(default=None, description="Extracted structured outcome data")
    error: Optional[str] = Field(default=None, description="Error message if call failed")
    duration_seconds: Optional[float] = Field(default=None, description="Call duration in seconds")
    cursor: Optional[str] = Field(default=None, description="Pagination or poll resumption cursor")
    raw_data: dict[str, Any] = Field(default_factory=dict, description="Full raw provider status response")


class CalleAdapterBase(ABC):
    """Abstract interface for all CloseLoop CALL-E provider adapters."""

    @abstractmethod
    def auth_status(self) -> AuthStatus:
        """Query current CALL-E authentication and session status."""

    @abstractmethod
    def tools_check(self) -> ToolsStatus:
        """Verify availability of necessary CALL-E MCP tools."""

    @abstractmethod
    def plan(self, request: PlanRequest) -> CallPlan:
        """Generate a CallPlan proposing dialogue goal, script, and recipient details."""

    @abstractmethod
    def inspect_plan(self, plan: CallPlan) -> PlanInspectionResult:
        """Mandatory inspection checkpoint: verify safety invariants before execution."""

    @abstractmethod
    def run(self, plan: CallPlan) -> CallRun:
        """Trigger telephony execution for an approved CallPlan."""

    @abstractmethod
    def status(self, run_id: str, cursor: Optional[str] = None) -> CallStatus:
        """Poll for active execution updates or final call results."""


class FakeAdapter(CalleAdapterBase):
    """Deterministic in-memory adapter simulating CALL-E for offline testing and dry runs."""

    FIXTURE_MATRIX: dict[str, dict[str, Any]] = {
        "confirmed": {
            "outcome_class": "confirmed",
            "structured_result": {"decision": "confirmed", "preferred_slot": "2026-09-04T15:00+05:30"},
            "evidence": {
                "source": "transcript",
                "speaker": "callee",
                "excerpt": "Yes, I will attend the interview at 3 PM.",
                "confidence": 0.96,
                "rationale": "Callee explicitly affirmed interview slot availability.",
            },
            "status": "completed",
            "duration_seconds": 45.2,
            "error": None,
        },
        "reschedule": {
            "outcome_class": "reschedule",
            "structured_result": {
                "decision": "reschedule",
                "preferred_slot": "2026-09-05T11:00+05:30",
                "reason": "Exam conflict with original timing",
            },
            "evidence": {
                "source": "transcript",
                "speaker": "callee",
                "excerpt": "I have an exam conflict then. Can we reschedule to tomorrow at 11am?",
                "confidence": 0.92,
                "rationale": "Callee declined current slot and provided an alternate preferred time.",
            },
            "status": "completed",
            "duration_seconds": 52.0,
            "error": None,
        },
        "declined": {
            "outcome_class": "declined",
            "structured_result": {"decision": "declined", "reason": "Accepted another offer"},
            "evidence": {
                "source": "transcript",
                "speaker": "callee",
                "excerpt": "I would like to withdraw my application as I accepted an offer elsewhere.",
                "confidence": 0.98,
                "rationale": "Callee explicitly declined participation.",
            },
            "status": "completed",
            "duration_seconds": 38.5,
            "error": None,
        },
        "no_answer": {
            "outcome_class": "no_answer",
            "structured_result": {"call_status": "no_answer", "rings": 6, "answered": False},
            "evidence": {
                "source": "telephony",
                "speaker": "ivr",
                "excerpt": "Ring timeout, destination party did not answer.",
                "confidence": 1.0,
                "rationale": "Telephony transport detected 6 rings without pickup.",
            },
            "status": "completed",
            "duration_seconds": 25.0,
            "error": None,
        },
        "voicemail": {
            "outcome_class": "voicemail",
            "structured_result": {"call_status": "voicemail", "beep_detected": True, "message_left": False},
            "evidence": {
                "source": "transcript",
                "speaker": "ivr",
                "excerpt": "Please leave your message after the tone. *beep*",
                "confidence": 0.95,
                "rationale": "Answering machine tone detected.",
            },
            "status": "completed",
            "duration_seconds": 18.0,
            "error": None,
        },
        "screening": {
            "outcome_class": "screening",
            "structured_result": {"call_status": "screening", "gatekeeper_action": "refused_transfer"},
            "evidence": {
                "source": "transcript",
                "speaker": "callee",
                "excerpt": "Who is calling? We do not accept unverified outreach calls.",
                "confidence": 0.89,
                "rationale": "Gatekeeper screening intercepted call.",
            },
            "status": "completed",
            "duration_seconds": 22.0,
            "error": None,
        },
        "wrong_person": {
            "outcome_class": "wrong_person",
            "structured_result": {"call_status": "wrong_person", "callee_clarification": "No one by that name here"},
            "evidence": {
                "source": "transcript",
                "speaker": "callee",
                "excerpt": "You have reached the wrong number, there is no candidate here.",
                "confidence": 0.94,
                "rationale": "Callee confirmed recipient identity mismatch.",
            },
            "status": "completed",
            "duration_seconds": 19.5,
            "error": None,
        },
        "callback_requested": {
            "outcome_class": "callback_requested",
            "structured_result": {"decision": "callback_requested", "callback_time": "2026-09-04T16:30+05:30"},
            "evidence": {
                "source": "transcript",
                "speaker": "callee",
                "excerpt": "I am driving right now. Please call me back at 4:30 PM.",
                "confidence": 0.91,
                "rationale": "Callee requested delayed contact.",
            },
            "status": "completed",
            "duration_seconds": 21.0,
            "error": None,
        },
        "hard_refusal": {
            "outcome_class": "hard_refusal",
            "structured_result": {"decision": "hard_refusal", "opt_out": True, "dnc": True},
            "evidence": {
                "source": "transcript",
                "speaker": "callee",
                "excerpt": "Stop calling me! Remove my number from your list immediately.",
                "confidence": 0.99,
                "rationale": "Explicit revoking of consent and do-not-call directive.",
            },
            "status": "completed",
            "duration_seconds": 15.0,
            "error": None,
        },
        "error": {
            "outcome_class": "error",
            "structured_result": {},
            "evidence": {
                "source": "telephony",
                "speaker": "provider",
                "excerpt": "SIP 504 Gateway Timeout",
                "confidence": 1.0,
                "rationale": "Carrier connection failed during call setup.",
            },
            "status": "failed",
            "duration_seconds": 30.0,
            "error": "SIP_TRANSPORT_TIMEOUT: Telephony gateway timed out after 30s",
        },
        "ambiguous": {
            "outcome_class": "ambiguous",
            "structured_result": {"decision": "unknown", "clarity": "unintelligible"},
            "evidence": {
                "source": "transcript",
                "speaker": "callee",
                "excerpt": "[inaudible static and background murmurs]",
                "confidence": 0.32,
                "rationale": "Speech recognition unable to parse definitive intent.",
            },
            "status": "completed",
            "duration_seconds": 14.0,
            "error": None,
        },
    }

    def __init__(
        self,
        default_outcome: str = "confirmed",
        rung_outcomes: Optional[dict[str, str]] = None,
        sequence_outcomes: Optional[list[str]] = None,
        authenticated: bool = True,
        tools_available: bool = True,
        poll_delay_steps: int = 0,
    ) -> None:
        """Initialize FakeAdapter with scenario configuration.

        Args:
            default_outcome: Fallback outcome fixture class.
            rung_outcomes: Mapping of specific rung names to outcome classes.
            sequence_outcomes: Sequential list of outcomes consumed on each call.
            authenticated: Whether auth_status reports successful authentication.
            tools_available: Whether tools_check reports all tools present.
            poll_delay_steps: Number of 'running' status polls before completing.
        """
        if default_outcome not in self.FIXTURE_MATRIX:
            raise ValueError(f"Unknown fixture outcome class: '{default_outcome}'")

        self.default_outcome = default_outcome
        self.rung_outcomes = rung_outcomes or {}
        self.sequence_outcomes = list(sequence_outcomes) if sequence_outcomes else []
        self._seq_index = 0
        self.is_authenticated = authenticated
        self.tools_are_available = tools_available
        self.poll_delay_steps = poll_delay_steps

        # Internal in-memory store
        self.plans: dict[str, CallPlan] = {}
        self.runs: dict[str, CallRun] = {}
        self.poll_counters: dict[str, int] = {}
        self.assigned_outcomes: dict[str, str] = {}

    def auth_status(self) -> AuthStatus:
        """Simulate authentication check."""
        if not self.is_authenticated:
            return AuthStatus(authenticated=False, user_id=None, expires_at=None, cached=False)
        return AuthStatus(
            authenticated=True,
            user_id="fake_user_evaluator",
            expires_at="2029-01-01T00:00:00Z",
            cached=True,
        )

    def tools_check(self) -> ToolsStatus:
        """Simulate MCP tools inspection."""
        if not self.tools_are_available:
            return ToolsStatus(available=False, tools=[], missing=list(SUPPORTED_TOOLS))
        return ToolsStatus(available=True, tools=list(SUPPORTED_TOOLS), missing=[])

    def plan(self, request: PlanRequest) -> CallPlan:
        """Generate a CallPlan with deterministic plan_id."""
        plan_hash = hashlib.sha256(f"{request.run_id}:{request.rung}:{request.phone}".encode("utf-8")).hexdigest()[:12]
        plan_id = f"plan_{plan_hash}"

        plan = CallPlan(
            plan_id=plan_id,
            rung=request.rung,
            phone=request.phone,
            goal=request.goal,
            script=request.script,
            language=request.language,
            recipient_role=request.recipient_role,
            approved=False,  # Unapproved by default until inspected
            raw_plan={"fake_simulated": True, "request": request.model_dump()},
        )
        self.plans[plan_id] = plan
        return plan

    def inspect_plan(self, plan: CallPlan) -> PlanInspectionResult:
        """Inspect CallPlan against domain boundary rules (Invariant 4 & 11)."""
        violations = check_domain_boundaries(plan.goal)
        if violations:
            plan.reject(f"Plan contains prohibited domain content: {', '.join(violations)}")
            return PlanInspectionResult(
                plan_id=plan.plan_id,
                approved=False,
                reason=plan.rejection_reason,
                domain_violations=violations,
            )

        # Plan is safe to execute
        plan.approve()
        return PlanInspectionResult(
            plan_id=plan.plan_id,
            approved=True,
            reason=None,
            domain_violations=[],
        )

    def run(self, plan: CallPlan) -> CallRun:
        """Initiate execution for an approved CallPlan.

        Raises SafetyViolationError if the plan has not passed inspection.
        """
        if not plan.approved:
            raise SafetyViolationError(
                f"Invariant 4 Violation: Cannot execute CallPlan '{plan.plan_id}'. "
                "Plan must be inspected and approved before execution is authorized."
            )

        # Determine outcome fixture for this attempt
        outcome_class = self._resolve_outcome(plan.rung)

        run_hash = hashlib.sha256(f"{plan.plan_id}:{len(self.runs)}".encode("utf-8")).hexdigest()[:12]
        external_id = f"calle_fake_{run_hash}"
        run_id = f"run_{run_hash}"

        initial_status = "running" if self.poll_delay_steps > 0 else self.FIXTURE_MATRIX[outcome_class]["status"]

        call_run = CallRun(
            run_id=run_id,
            external_call_id=external_id,
            plan_id=plan.plan_id,
            status=initial_status,
            started_at=datetime.now(timezone.utc),
            completed_at=None if initial_status == "running" else datetime.now(timezone.utc),
            structured_result=None if initial_status == "running" else self.FIXTURE_MATRIX[outcome_class]["structured_result"],
            raw_output={"outcome_class": outcome_class, "fixture": self.FIXTURE_MATRIX[outcome_class]},
            error=self.FIXTURE_MATRIX[outcome_class].get("error"),
        )

        self.runs[run_id] = call_run
        self.poll_counters[run_id] = 0
        self.assigned_outcomes[run_id] = outcome_class
        return call_run

    def status(self, run_id: str, cursor: Optional[str] = None) -> CallStatus:
        """Return call execution status, simulating bounded polling if configured."""
        if run_id not in self.runs:
            return CallStatus(
                run_id=run_id,
                status="failed",
                error=f"Run '{run_id}' not found in FakeAdapter registry",
            )

        run = self.runs[run_id]
        outcome_class = self.assigned_outcomes.get(run_id, self.default_outcome)
        fixture = self.FIXTURE_MATRIX[outcome_class]

        # Handle bounded polling progression
        if run.status == "running":
            self.poll_counters[run_id] += 1
            if self.poll_counters[run_id] >= self.poll_delay_steps:
                # Transition to terminal state
                run.status = fixture["status"]
                run.completed_at = datetime.now(timezone.utc)
                run.structured_result = fixture["structured_result"]
                run.error = fixture.get("error")

        return CallStatus(
            run_id=run_id,
            external_call_id=run.external_call_id,
            status=run.status,
            structured_result=run.structured_result,
            error=run.error,
            duration_seconds=fixture.get("duration_seconds", 30.0),
            raw_data=fixture,
        )

    def _resolve_outcome(self, rung: str) -> str:
        """Resolve which outcome fixture to serve based on sequence, rung, or default."""
        if self._seq_index < len(self.sequence_outcomes):
            outcome = self.sequence_outcomes[self._seq_index]
            self._seq_index += 1
            if outcome in self.FIXTURE_MATRIX:
                return outcome

        if rung in self.rung_outcomes:
            outcome = self.rung_outcomes[rung]
            if outcome in self.FIXTURE_MATRIX:
                return outcome

        return self.default_outcome
