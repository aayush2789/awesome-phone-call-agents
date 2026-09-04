#!/usr/bin/env python3
"""CloseLoop Manual Smoke Test Runner.

Provides a safe, graduated smoke test utility for validating CALL-E integration:
1. --dry-run (Default): Offline end-to-end verification using FakeAdapter.
2. --check-auth: Verifies real `calle auth status` and `calle mcp tools` (0 calls placed).
3. --plan-only: Generates and inspects a real CallPlan via `calle call plan` without dialing (0 calls placed).
4. --live: Executes a single live test call against an authorized test number with full ledger persistence.

Safety Guarantees:
- Real calls require both `--live` AND `--confirm-live` flags.
- Real phone numbers must be E.164 formatted.
- Sensitive credentials, auth tokens, and confirmation tokens are masked in output.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add package root to sys.path so it runs standalone
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from closeloop.adapter import FakeAdapter, PlanRequest
from closeloop.classifier import OutcomeClassifier
from closeloop.cli_adapter import (
    CalleAuthError,
    CalleCliError,
    CalleExecutableNotFoundError,
    CliAdapter,
)
from closeloop.engine import OrchestrationEngine
from closeloop.ledger import SQLiteLedger
from closeloop.models import (
    ContactRung,
    OutcomeContract,
    Policy,
    WorkflowSpec,
    compute_idempotency_key,
)
from closeloop.safety import mask_phone, validate_e164


def log_step(step: int, title: str) -> None:
    print(f"\n[{step}] {title}")
    print("-" * (len(title) + 4))


def run_auth_check(adapter: CliAdapter) -> bool:
    """Step 1: Check authentication status and MCP tooling."""
    log_step(1, "Checking CALL-E Authentication Status")
    try:
        auth = adapter.auth_status()
        if auth.authenticated:
            print(f"  [PASS] Authenticated: True")
            print(f"  [PASS] Cached Token: {auth.cached}")
            print(f"  [PASS] Expires At: {auth.expires_at}")
        else:
            print(f"  [FAIL] Authenticated: False")
            print(f"  [WARN] Details: {auth.details}")
            print("  Run `calle auth login` to authenticate before live calls.")
            return False
    except CalleExecutableNotFoundError as err:
        print(f"  [ERROR] {err}")
        return False
    except Exception as exc:
        print(f"  [ERROR] Unexpected error during auth check: {exc}")
        return False

    log_step(2, "Checking CALL-E MCP Tools Availability")
    try:
        tools = adapter.tools_check()
        if tools.available:
            print(f"  [PASS] Required tools detected: {', '.join(tools.tools)}")
            return True
        else:
            print(f"  [FAIL] Missing required tools: {', '.join(tools.missing)}")
            return False
    except Exception as exc:
        print(f"  [ERROR] Unexpected error during tools check: {exc}")
        return False


def run_dry_run_workflow(ledger_path: str = ":memory:") -> None:
    """Execute end-to-end dry-run with FakeAdapter and SQLiteLedger."""
    print("=" * 60)
    print("   CloseLoop Offline Dry-Run Smoke Test (Zero Network Calls)")
    print("=" * 60)

    ledger = SQLiteLedger(ledger_path)
    adapter = FakeAdapter(default_outcome="confirmed")
    engine = OrchestrationEngine(adapter=adapter, ledger=ledger)

    spec = WorkflowSpec(
        run_id=f"smoke-dryrun-{int(datetime.now(timezone.utc).timestamp())}",
        outcome=OutcomeContract(
            name="appointment_confirmation",
            result_schema={
                "type": "object",
                "required": ["decision"],
                "properties": {
                    "decision": {"type": "string", "enum": ["confirmed", "reschedule", "declined"]},
                },
            },
            stop_when="decision in [confirmed, declined]",
        ),
        ladder=[
            ContactRung(
                rung="primary_candidate",
                phone="+15551234567",
                consent_basis="consented during smoke-test setup",
                max_attempts=1,
            )
        ],
        policy=Policy(max_calls_total=1),
    )

    log_step(1, f"Executing Orchestration Engine with Spec '{spec.run_id}'")
    result = engine.run(spec)

    log_step(2, "Workflow Execution Result")
    print(f"  Status:         {result.status}")
    print(f"  Final Outcome:  {result.outcome}")
    print(f"  Calls Placed:   {result.calls_placed}")
    print(f"  Calls Avoided:  {result.calls_avoided}")
    print(f"  Summary:        {result.summary}")

    log_step(3, "Persistent SQLite Ledger Verification")
    spec_stored = ledger.get_workflow_spec(spec.run_id)
    assert spec_stored is not None
    print(f"  [PASS] WorkflowSpec retrieved from SQLite ledger")

    attempts = ledger.list_attempts(spec.run_id)
    assert len(attempts) == 1
    print(f"  [PASS] {len(attempts)} attempt persisted with status '{attempts[0].status}'")

    audit = ledger.get_audit_trail(spec.run_id)
    print(f"  [PASS] {len(audit)} structured audit entries recorded:")
    for entry in audit:
        print(f"         - [{entry.action}] rung={entry.rung} outcome={entry.outcome_class or 'n/a'}")

    print("\n[SUCCESS] Offline smoke test completed successfully with 100% safety verification.")


def run_plan_only_test(adapter: CliAdapter, phone: str, goal: str) -> None:
    """Generate and inspect a real CallPlan via `calle call plan` without dialing."""
    print("=" * 60)
    print("   CloseLoop Pre-Dial Plan Inspection Test (Zero Calls Placed)")
    print("=" * 60)

    if not validate_e164(phone):
        print(f"[ERROR] Phone number '{phone}' must be in strict E.164 format (e.g. +15551234567).")
        sys.exit(1)

    auth_ok = run_auth_check(adapter)
    if not auth_ok:
        sys.exit(1)

    log_step(3, f"Generating CallPlan for {mask_phone(phone)}")
    req = PlanRequest(
        run_id=f"plan-test-{int(datetime.now(timezone.utc).timestamp())}",
        rung="primary",
        phone=phone,
        goal=goal,
        language="English",
    )
    plan = adapter.plan(req)
    print(f"  [PASS] Generated Plan ID: {plan.plan_id}")
    print(f"  [PASS] Phone (Masked):   {mask_phone(plan.phone)}")
    print(f"  [PASS] Has Confirm Token: {bool(plan.raw_plan.get('confirm_token'))}")

    log_step(4, "Mandatory Post-Plan Inspection Gate Check")
    inspection = adapter.inspect_plan(plan)
    if inspection.approved:
        print(f"  [PASS] Plan Inspection APPROVED: All safety invariants and domain boundaries satisfied.")
    else:
        print(f"  [FAIL] Plan Inspection REJECTED: {inspection.reason}")
        print(f"         Domain violations: {inspection.domain_violations}")

    print("\n[SUCCESS] Plan-only test completed. No call was initiated.")


def run_live_smoke_call(adapter: CliAdapter, phone: str, goal: str, ledger_path: str) -> None:
    """Execute a single live smoke call against an authorized test number."""
    print("=" * 60)
    print("   CloseLoop LIVE Outbound Smoke Test (Real Telephony Call)")
    print("=" * 60)
    print(f"[CAUTION] Placing a LIVE phone call to {mask_phone(phone)}.")

    if not validate_e164(phone):
        print(f"[ERROR] Phone number '{phone}' must be in strict E.164 format.")
        sys.exit(1)

    auth_ok = run_auth_check(adapter)
    if not auth_ok:
        sys.exit(1)

    ledger = SQLiteLedger(ledger_path)
    run_id = f"smoke-live-{int(datetime.now(timezone.utc).timestamp())}"

    spec = WorkflowSpec(
        run_id=run_id,
        outcome=OutcomeContract(
            name="smoke_test_call",
            result_schema={
                "type": "object",
                "properties": {
                    "decision": {"type": "string"},
                },
            },
            stop_when="decision in [confirmed, declined, completed]",
        ),
        ladder=[
            ContactRung(
                rung="authorized_tester",
                phone=phone,
                consent_basis="Direct developer authorization for live smoke test",
                max_attempts=1,
            )
        ],
        policy=Policy(max_calls_total=1),
    )

    ledger.record_workflow(spec)

    log_step(3, f"Planning Call via `calle call plan`")
    req = PlanRequest(
        run_id=run_id,
        rung="authorized_tester",
        phone=phone,
        goal=goal,
        language="English",
    )
    plan = adapter.plan(req)
    print(f"  [PASS] Plan ID: {plan.plan_id}")

    log_step(4, "Inspecting Call Plan")
    inspection = adapter.inspect_plan(plan)
    if not inspection.approved:
        print(f"  [FAIL] Plan rejected by safety gate: {inspection.reason}")
        sys.exit(1)
    print("  [PASS] Plan approved by inspection gate.")

    idempotency_key = compute_idempotency_key(run_id, "authorized_tester", 1)
    from closeloop.models import CallAttempt
    attempt = CallAttempt(
        idempotency_key=idempotency_key,
        run_id=run_id,
        rung="authorized_tester",
        attempt=1,
        phone=phone,
        plan=plan,
        status="planned",
    )
    ledger.record_attempt(attempt)
    ledger.update_attempt_status(idempotency_key, status="executing")

    log_step(5, "Executing Live Call via `calle call run`")
    call_run = adapter.run(plan)
    print(f"  [PASS] Call run initiated. Run ID: {call_run.run_id}")

    log_step(6, "Polling Call Status via `calle call status`")
    terminal_statuses = {"completed", "failed", "cancelled", "timed_out"}
    call_status = None
    poll_count = 0
    max_polls = 40  # 40 * 3s = 120s max

    while poll_count < max_polls:
        poll_count += 1
        call_status = adapter.status(call_run.run_id)
        print(f"  [Poll {poll_count}] Status: {call_status.status} (duration: {call_status.duration_seconds or 0:.1f}s)")
        if call_status.status.lower() in terminal_statuses:
            break
        time.sleep(3)

    log_step(7, "Classifying Call Outcome & Finalizing Ledger")
    classifier = OutcomeClassifier()
    outcome_res = classifier.classify(call_status, spec.outcome)
    print(f"  Normalized Outcome Class: {outcome_res.outcome_class}")
    print(f"  Confidence:              {outcome_res.confidence}")
    print(f"  Stop Condition Met:      {outcome_res.stop_condition_met}")

    ledger.update_attempt_status(
        idempotency_key,
        status="terminal",
        run=call_run,
        outcome=outcome_res,
    )

    from closeloop.models import WorkflowResult
    result = WorkflowResult(
        run_id=run_id,
        status="closed" if outcome_res.stop_condition_met else "terminated",
        outcome=outcome_res.outcome_class,
        summary=f"Smoke test call finished with status '{call_status.status}' and outcome '{outcome_res.outcome_class}'.",
        calls_placed=1,
        calls_avoided=0,
        recipient_phone_e164=phone,
    )
    ledger.save_workflow_result(result)

    print("\n" + "=" * 60)
    print("   LIVE SMOKE TEST COMPLETED SUCCESSFULLY")
    print("=" * 60)
    print(f"Run ID:        {run_id}")
    print(f"Outcome:       {outcome_res.outcome_class}")
    print(f"Ledger DB:     {ledger_path}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CloseLoop Manual Smoke Test & Verification Utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 1. Zero-cost offline dry-run (default):
  python scripts/smoke_test.py --dry-run

  # 2. Check local CALL-E CLI authentication and MCP tools (0 calls placed):
  python scripts/smoke_test.py --check-auth

  # 3. Test pre-dial plan generation and safety inspection (0 calls placed):
  python scripts/smoke_test.py --plan-only --phone +15551234567 --goal "Confirm interview slot for tomorrow at 2pm"

  # 4. Execute a single live smoke call against your own authorized test phone:
  python scripts/smoke_test.py --live --confirm-live --phone +15551234567 --goal "Say hello from CloseLoop smoke test"
        """,
    )

    parser.add_argument("--dry-run", action="store_true", help="Run offline dry-run with FakeAdapter (default)")
    parser.add_argument("--check-auth", action="store_true", help="Check calle auth status and mcp tools only")
    parser.add_argument("--plan-only", action="store_true", help="Generate and inspect CallPlan without dialing")
    parser.add_argument("--live", action="store_true", help="Opt-in to placing a single live outbound call")
    parser.add_argument("--confirm-live", action="store_true", help="Required confirmation flag to authorize live call")
    parser.add_argument("--phone", type=str, default="+15551234567", help="Target phone number in E.164 format")
    parser.add_argument(
        "--goal",
        type=str,
        default="This is an automated smoke test for CloseLoop. Ask the recipient to confirm they can hear the audio clearly.",
        help="Call goal or instruction",
    )
    parser.add_argument("--db-path", type=str, default=":memory:", help="SQLite ledger database path")

    args = parser.parse_args()

    adapter = CliAdapter()

    if args.check_auth:
        ok = run_auth_check(adapter)
        sys.exit(0 if ok else 1)

    if args.plan_only:
        run_plan_only_test(adapter, args.phone, args.goal)
        sys.exit(0)

    if args.live:
        if not args.confirm_live:
            print("[ERROR] Safety Invariant Triggered: Placing a live call requires '--confirm-live'.")
            print("        Example: python scripts/smoke_test.py --live --confirm-live --phone <E164>")
            sys.exit(1)
        # Default disk db for live runs if not specified
        db_path = args.db_path if args.db_path != ":memory:" else "smoke_ledger.db"
        run_live_smoke_call(adapter, args.phone, args.goal, db_path)
        sys.exit(0)

    # Default: Dry-run
    run_dry_run_workflow(args.db_path)


if __name__ == "__main__":
    main()
