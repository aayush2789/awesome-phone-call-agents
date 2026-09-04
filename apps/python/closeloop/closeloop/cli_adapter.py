"""CloseLoop CALL-E CLI Adapter (Phase 9).

Wraps the official `calle` CLI binary (`calle auth status`, `calle mcp tools`,
`calle call plan`, `calle call run`, `calle call status`).
Normalizes CLI JSON outputs into CloseLoop domain models, protects sensitive
credentials/tokens from appearing in logs, and enforces the mandatory pre-execution
inspection checkpoint.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, Optional

from closeloop.adapter import (
    ALL_OUTCOME_CLASSES,
    AuthStatus,
    CalleAdapterBase,
    CallStatus,
    PlanInspectionResult,
    PlanRequest,
    SUPPORTED_TOOLS,
    ToolsStatus,
)
from closeloop.models import CallPlan, CallRun
from closeloop.safety import (
    SafetyViolationError,
    check_domain_boundaries,
    mask_phone,
    validate_e164,
)

logger = logging.getLogger("closeloop.cli_adapter")


class CalleCliError(Exception):
    """Base exception for CALL-E CLI adapter failures."""


class CalleExecutableNotFoundError(CalleCliError):
    """Raised when the `calle` CLI binary is not found on PATH."""


class CalleAuthError(CalleCliError):
    """Raised when CALL-E CLI is unauthenticated or token is expired."""


class CalleToolMissingError(CalleCliError):
    """Raised when required MCP tools are missing from the CALL-E server."""


class CalleExecutionError(CalleCliError):
    """Raised when a `calle` sub-command fails or exits with non-zero code."""

    def __init__(self, command: str, returncode: int, stderr: str) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"Command '{command}' failed with exit code {returncode}: {stderr.strip()}")


class CliAdapter(CalleAdapterBase):
    """Production CALL-E adapter using the local `calle` CLI.

    Executes CLI commands via subprocess, parses structured JSON outputs,
    and seamlessly bridges CALL-E's MCP tooling with CloseLoop's safety-first
    state machine and persistent ledger.
    """

    def __init__(
        self,
        executable: str = "calle",
        timeout_seconds: int = 150,
        extra_args: Optional[list[str]] = None,
    ) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.extra_args = extra_args or ["--no-telemetry"]

    def _resolve_executable(self) -> str:
        """Find the full path to the calle binary."""
        path = shutil.which(self.executable)
        if not path:
            # Also check common npm/global locations on Windows if not on direct PATH
            if os.name == "nt":
                app_data = os.environ.get("APPDATA", "")
                candidate = os.path.join(app_data, "npm", "calle.cmd")
                if os.path.isfile(candidate):
                    return candidate
            raise CalleExecutableNotFoundError(
                f"The '{self.executable}' CLI binary was not found on PATH. "
                "Ensure CALL-E CLI is installed via `npm install -g @call-e/calle`."
            )
        return path

    def _run_command(self, sub_args: list[str], timeout: Optional[int] = None) -> dict[str, Any]:
        """Execute a calle command, capture stdout, and parse JSON response safely."""
        exe = self._resolve_executable()
        cmd = [exe] + sub_args + self.extra_args + ["--json"]

        # Log command without leaking potential tokens or phone numbers
        sanitized_cmd = []
        skip_next = False
        for arg in cmd:
            if skip_next:
                sanitized_cmd.append("***")
                skip_next = False
            elif arg in ("--confirm-token", "--token"):
                sanitized_cmd.append(arg)
                skip_next = True
            elif arg == "--to-phone":
                sanitized_cmd.append(arg)
                skip_next = True
            else:
                sanitized_cmd.append(arg)

        logger.debug("Executing CLI command: %s", " ".join(sanitized_cmd))

        effective_timeout = timeout or self.timeout_seconds
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CalleExecutionError(
                command=" ".join(sub_args),
                returncode=-1,
                stderr=f"Command timed out after {effective_timeout}s.",
            ) from exc
        except Exception as exc:
            raise CalleCliError(f"Failed to execute '{sub_args[0]}': {exc}") from exc

        if res.returncode != 0:
            err_msg = res.stderr or res.stdout or f"Exit code {res.returncode}"
            raise CalleExecutionError(
                command=" ".join(sub_args),
                returncode=res.returncode,
                stderr=err_msg,
            )

        try:
            return json.loads(res.stdout)
        except json.JSONDecodeError as err:
            raise CalleCliError(
                f"Failed to parse JSON response from 'calle {' '.join(sub_args)}': {res.stdout[:200]}"
            ) from err

    def auth_status(self) -> AuthStatus:
        """Query current CALL-E authentication status via `calle auth status --json`."""
        try:
            data = self._run_command(["auth", "status"], timeout=15)
            # CALL-E auth status format:
            # {"server_url": "...", "usable": true, "expires_at": "...", "cache_exists": true, ...}
            is_usable = bool(data.get("usable") or data.get("authenticated"))
            expires_at = data.get("expires_at")
            cached = bool(data.get("cache_exists", True))

            return AuthStatus(
                authenticated=is_usable,
                expires_at=expires_at,
                cached=cached,
                details=data,
            )
        except CalleExecutionError as err:
            return AuthStatus(
                authenticated=False,
                cached=False,
                details={"error": err.stderr, "returncode": err.returncode},
            )
        except Exception as exc:
            return AuthStatus(
                authenticated=False,
                cached=False,
                details={"error": str(exc)},
            )

    def tools_check(self) -> ToolsStatus:
        """Verify availability of necessary CALL-E MCP tools via `calle mcp tools --json`."""
        try:
            data = self._run_command(["mcp", "tools"], timeout=20)
            # Output contains tools list under "tools" or "result.tools"
            raw_tools = data.get("tools") or data.get("result", {}).get("tools", [])
            tool_names = set()
            for t in raw_tools:
                if isinstance(t, dict) and "name" in t:
                    tool_names.add(t["name"])
                elif isinstance(t, str):
                    tool_names.add(t)

            missing = [req for req in SUPPORTED_TOOLS if req not in tool_names]
            return ToolsStatus(
                available=len(missing) == 0,
                tools=sorted(list(tool_names)),
                missing=missing,
                details=data if len(tool_names) < 10 else {"tool_count": len(tool_names)},
            )
        except Exception as exc:
            return ToolsStatus(
                available=False,
                tools=[],
                missing=list(SUPPORTED_TOOLS),
                details={"error": str(exc)},
            )

    def plan(self, request: PlanRequest) -> CallPlan:
        """Generate a CallPlan proposing dialogue goal, script, and recipient details via `calle call plan`."""
        sub_args = [
            "call",
            "plan",
            "--to-phone",
            request.phone,
            "--goal",
            request.goal,
        ]
        if request.language:
            sub_args.extend(["--language", request.language])
        if "region" in request.context:
            sub_args.extend(["--region", str(request.context["region"])])
        if "timezone" in request.context:
            sub_args.extend(["--timezone", str(request.context["timezone"])])

        data = self._run_command(sub_args, timeout=150)
        structured = data.get("result", {}).get("structuredContent", data)

        plan_id = structured.get("plan_id") or data.get("plan_id")
        if not plan_id:
            # Fallback if structure differs
            plan_id = f"plan_{int(datetime.now(timezone.utc).timestamp())}"

        # Note: confirm_token is required for calle call run and must be retained
        confirm_token = structured.get("confirm_token") or data.get("confirm_token")

        raw_plan = dict(data)
        if confirm_token:
            raw_plan["confirm_token"] = confirm_token

        return CallPlan(
            plan_id=plan_id,
            rung=request.rung,
            phone=request.phone,
            goal=request.goal,
            prompt=request.script or request.goal,
            language=request.language or "English",
            recipient_role=request.recipient_role,
            approved=False,
            raw_plan=raw_plan,
        )

    def inspect_plan(self, plan: CallPlan) -> PlanInspectionResult:
        """Mandatory pre-dial inspection gate (Invariants 4 & 11).

        Inspects the generated CallPlan before authorizing execution:
        - Checks domain boundaries (medical, legal, financial, emergency).
        - Verifies phone number matches strict E.164.
        - Verifies required confirm_token exists in raw_plan.
        """
        violations: list[str] = []

        # 1. Domain boundaries
        violations.extend(check_domain_boundaries(plan.goal))

        if plan.prompt and plan.prompt != plan.goal:
            for v in check_domain_boundaries(plan.prompt):
                if v not in violations:
                    violations.append(v)

        # 2. E.164 Recipient format
        if not validate_e164(plan.phone):
            violations.append(f"Phone number '{mask_phone(plan.phone)}' is not valid E.164 format.")

        # 3. Execution readiness
        confirm_token = plan.raw_plan.get("confirm_token")
        if not confirm_token and not plan.raw_plan.get("ready_to_run", True):
            violations.append("Call plan is marked not ready_to_run by provider.")

        approved = len(violations) == 0
        reason = None if approved else "; ".join(violations)

        if approved:
            plan.approve()
        else:
            plan.reject(reason or "Safety boundary violation")

        return PlanInspectionResult(
            plan_id=plan.plan_id,
            approved=approved,
            reason=reason,
            domain_violations=violations,
        )

    def run(self, plan: CallPlan) -> CallRun:
        """Trigger telephony execution for an approved CallPlan via `calle call run`."""
        if not plan.approved:
            raise SafetyViolationError(
                f"Cannot execute unapproved CallPlan '{plan.plan_id}'. "
                f"Inspection rejection reason: {plan.rejection_reason or 'Not inspected'}"
            )

        confirm_token = plan.raw_plan.get("confirm_token")
        sub_args = ["call", "run", "--plan-id", plan.plan_id]
        if confirm_token:
            sub_args.extend(["--confirm-token", confirm_token])

        data = self._run_command(sub_args, timeout=60)
        structured = data.get("result", {}).get("structuredContent", data)

        run_id = structured.get("run_id") or data.get("run_id") or plan.plan_id
        external_id = structured.get("call_id") or data.get("call_id")
        status_val = structured.get("status") or data.get("status") or "running"

        return CallRun(
            run_id=run_id,
            external_call_id=external_id,
            plan_id=plan.plan_id,
            status=status_val.lower(),
            started_at=datetime.now(timezone.utc),
            raw_output=data,
        )

    def status(self, run_id: str, cursor: Optional[str] = None) -> CallStatus:
        """Poll for active execution updates or final results via `calle call status`."""
        sub_args = ["call", "status", "--run-id", run_id]
        if cursor:
            sub_args.extend(["--cursor", cursor])

        data = self._run_command(sub_args, timeout=30)
        structured = data.get("result", {}).get("structuredContent", data)

        status_val = structured.get("status") or data.get("status") or "running"
        external_id = structured.get("call_id") or data.get("call_id")
        duration = structured.get("duration_seconds") or data.get("duration_seconds")
        err = structured.get("error") or data.get("error")
        new_cursor = structured.get("cursor") or data.get("cursor")

        extracted_result = structured.get("structured_result") or data.get("structured_result")

        return CallStatus(
            run_id=run_id,
            external_call_id=external_id,
            status=status_val.lower(),
            structured_result=extracted_result,
            error=err,
            duration_seconds=float(duration) if duration is not None else None,
            cursor=new_cursor,
            raw_data=data,
        )
