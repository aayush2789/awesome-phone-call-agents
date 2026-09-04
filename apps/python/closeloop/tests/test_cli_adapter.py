"""Unit tests for Phase 9: Real CLI Adapter (CliAdapter)."""

import json
import subprocess
from unittest.mock import MagicMock, patch
import pytest

from closeloop.adapter import PlanRequest
from closeloop.cli_adapter import (
    CalleCliError,
    CalleExecutableNotFoundError,
    CalleExecutionError,
    CliAdapter,
)
from closeloop.models import CallPlan
from closeloop.safety import SafetyViolationError


@pytest.fixture
def mock_which():
    with patch("shutil.which", return_value="C:\\FakePath\\calle.cmd") as mock:
        yield mock


def test_resolve_executable_raises_when_not_found():
    """Verify CalleExecutableNotFoundError is raised when calle binary cannot be located."""
    with patch("shutil.which", return_value=None), patch("os.path.isfile", return_value=False):
        adapter = CliAdapter(executable="calle_nonexistent")
        with pytest.raises(CalleExecutableNotFoundError):
            adapter._resolve_executable()


def test_auth_status_success(mock_which):
    """Verify auth_status returns authenticated=True on valid CLI token JSON."""
    auth_payload = {
        "server_url": "https://seleven-mcp-sg.airudder.com/mcp/openagent_oauth",
        "cache_exists": True,
        "usable": True,
        "expires_at": "2029-01-01T00:00:00Z",
    }
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(auth_payload),
            stderr="",
        )
        adapter = CliAdapter()
        status = adapter.auth_status()

        assert status.authenticated is True
        assert status.expires_at == "2029-01-01T00:00:00Z"
        assert status.cached is True
        # Verify --json was passed
        cmd = mock_run.call_args[0][0]
        assert "auth" in cmd
        assert "status" in cmd
        assert "--json" in cmd


def test_auth_status_failure(mock_which):
    """Verify auth_status returns authenticated=False when CLI reports failure or unusable token."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: token expired or not logged in",
        )
        adapter = CliAdapter()
        status = adapter.auth_status()

        assert status.authenticated is False
        assert status.cached is False
        assert "token expired" in str(status.details)


def test_tools_check_all_available(mock_which):
    """Verify tools_check returns available=True when plan_call, run_call, get_call_run are present."""
    tools_payload = {
        "tools": [
            {"name": "plan_call"},
            {"name": "run_call"},
            {"name": "get_call_run"},
            {"name": "track_ui_events"},
        ]
    }
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(tools_payload),
            stderr="",
        )
        adapter = CliAdapter()
        res = adapter.tools_check()

        assert res.available is True
        assert "plan_call" in res.tools
        assert "run_call" in res.tools
        assert "get_call_run" in res.tools
        assert len(res.missing) == 0


def test_tools_check_missing_tools(mock_which):
    """Verify tools_check identifies missing tools."""
    tools_payload = {
        "tools": [
            {"name": "plan_call"},
        ]
    }
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(tools_payload),
            stderr="",
        )
        adapter = CliAdapter()
        res = adapter.tools_check()

        assert res.available is False
        assert "run_call" in res.missing
        assert "get_call_run" in res.missing


def test_plan_call_generation(mock_which):
    """Verify plan formats CLI arguments correctly and retains confirm_token."""
    plan_payload = {
        "plan_id": "plan_real_001",
        "ready_to_run": True,
        "confirm_token": "secret_token_abc123",
    }
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(plan_payload),
            stderr="",
        )
        adapter = CliAdapter()
        req = PlanRequest(
            run_id="run_99",
            rung="primary",
            phone="+15551234567",
            goal="Confirm appointment for tomorrow at 2pm",
            language="English",
            context={"region": "US", "timezone": "America/New_York"},
        )
        plan = adapter.plan(req)

        assert plan.plan_id == "plan_real_001"
        assert plan.phone == "+15551234567"
        assert plan.goal == "Confirm appointment for tomorrow at 2pm"
        assert plan.raw_plan.get("confirm_token") == "secret_token_abc123"
        assert plan.approved is False

        # Verify command arguments passed to subprocess
        cmd = mock_run.call_args[0][0]
        assert "call" in cmd
        assert "plan" in cmd
        assert "--to-phone" in cmd
        assert "+15551234567" in cmd
        assert "--region" in cmd
        assert "US" in cmd


def test_inspect_plan_clean_passes(mock_which):
    """Verify clean plan passes the inspection gate."""
    adapter = CliAdapter()
    plan = CallPlan(
        plan_id="plan_test_01",
        rung="primary",
        phone="+15551234567",
        goal="Logistics coordination for product delivery",
        prompt="Coordinate delivery window",
        raw_plan={"confirm_token": "token_xyz"},
    )
    result = adapter.inspect_plan(plan)

    assert result.approved is True
    assert plan.approved is True
    assert len(result.domain_violations) == 0


def test_inspect_plan_prohibited_domain_rejected(mock_which):
    """Verify plan with prohibited domain content is rejected by inspection gate."""
    adapter = CliAdapter()
    plan = CallPlan(
        plan_id="plan_test_02",
        rung="primary",
        phone="+15551234567",
        goal="Provide medical dosage adjustment and emergency diagnosis",
        prompt="Tell patient to take 50mg of medication",
        raw_plan={"confirm_token": "token_xyz"},
    )
    result = adapter.inspect_plan(plan)

    assert result.approved is False
    assert plan.approved is False
    assert len(result.domain_violations) > 0
    assert "medical" in (result.reason or "").lower()


def test_run_unapproved_plan_raises(mock_which):
    """Verify run raises SafetyViolationError if the plan has not been approved."""
    adapter = CliAdapter()
    plan = CallPlan(
        plan_id="plan_unapproved",
        rung="primary",
        phone="+15551234567",
        goal="Test goal",
        approved=False,
    )
    with pytest.raises(SafetyViolationError, match="Cannot execute unapproved"):
        adapter.run(plan)


def test_run_approved_plan_executes_call(mock_which):
    """Verify run executes calle call run with plan_id and confirm_token."""
    run_payload = {
        "run_id": "call_run_777",
        "call_id": "ext_call_888",
        "status": "running",
    }
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(run_payload),
            stderr="",
        )
        adapter = CliAdapter()
        plan = CallPlan(
            plan_id="plan_approved_01",
            rung="primary",
            phone="+15551234567",
            goal="Valid coordination goal",
            approved=True,
            raw_plan={"confirm_token": "secret_token_456"},
        )
        call_run = adapter.run(plan)

        assert call_run.run_id == "call_run_777"
        assert call_run.external_call_id == "ext_call_888"
        assert call_run.status == "running"

        cmd = mock_run.call_args[0][0]
        assert "call" in cmd
        assert "run" in cmd
        assert "--plan-id" in cmd
        assert "plan_approved_01" in cmd
        assert "--confirm-token" in cmd
        assert "secret_token_456" in cmd


def test_status_polling(mock_which):
    """Verify status polls calle call status and parses extracted result."""
    status_payload = {
        "run_id": "call_run_777",
        "call_id": "ext_call_888",
        "status": "completed",
        "duration_seconds": 42.5,
        "structured_result": {"decision": "confirmed", "preferred_slot": "2026-09-10T10:00:00Z"},
    }
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(status_payload),
            stderr="",
        )
        adapter = CliAdapter()
        res = adapter.status("call_run_777")

        assert res.run_id == "call_run_777"
        assert res.status == "completed"
        assert res.duration_seconds == 42.5
        assert res.structured_result == {"decision": "confirmed", "preferred_slot": "2026-09-10T10:00:00Z"}

        cmd = mock_run.call_args[0][0]
        assert "call" in cmd
        assert "status" in cmd
        assert "--run-id" in cmd
        assert "call_run_777" in cmd


def test_command_timeout_normalization(mock_which):
    """Verify timeout translates into CalleExecutionError."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["calle"], timeout=5)):
        adapter = CliAdapter()
        with pytest.raises(CalleExecutionError, match="timed out"):
            adapter._run_command(["call", "plan"])
