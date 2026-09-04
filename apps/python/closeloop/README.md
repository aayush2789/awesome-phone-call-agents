# CloseLoop Runner

`closeloop` is an outcome-driven phone-call orchestration layer built on top of CALL-E.

CloseLoop treats voice calling as an expensive, restricted input/output operation. Rather than optimizing for total calls placed, CloseLoop optimizes for closing structured business outcomes with the minimum number of calls necessary.

## Key Capabilities

- **Declarative Workflows**: Define outcomes, escalation rungs, JSON schemas, stop conditions, and routing policies in YAML.
- **Fail-Closed Safety Engine**: Enforces consent basis, valid E.164 numbers, quiet hours across IANA timezones, call budget limits, persistent suppression lists, and an out-of-band kill switch.
- **Mandatory Plan Inspection**: Enforces an explicit safety gate between CALL-E planning and execution to inspect recipient numbers and rendered goals.
- **Outcome Routing**: Normalizes raw telephony outcomes into generic outcome classes (`confirmed`, `reschedule`, `declined`, `no_answer`, `voicemail`, `hard_refusal`, etc.) and routes them according to workflow policy.
- **Crash-Safe Idempotency**: Employs an execution ledger to prevent accidental duplicate calls after a restart.
- **Dry-Run by Default**: Ships with `FakeAdapter` simulating all outcome classes without spending real CALL-E calls or dialing actual phone numbers.

## Safety & Credential Handling

- **No Stored Credentials**: CloseLoop relies on CALL-E's local token cache or standard environment variables. Tokens, passwords, cookies, or confirmation tokens are never printed or logged.
- **Phone Number Masking**: All user-facing outputs and summaries mask phone numbers (e.g., `+1555010****`).
- **Fictional Test Data**: Tests and examples use standard RFC fictional numbers (`+1555010XXXX`).
- **Hard Refusals**: Explicit opt-out immediately terminates the workflow chain and adds the contact to the suppression registry.
- **Quiet Hours**: Calls are blocked if the local time in the recipient's declared timezone falls within the quiet hours window.

## Installation

```bash
cd apps/python/closeloop
pip install -e .
```

## Running Tests

Run the safety and orchestration unit tests:

```bash
pytest
```

## Dry-Run Usage (Default)

Execute a sample workflow in dry-run mode (no live calls placed):

```bash
python -m closeloop run examples/placement.yaml --dry-run
```
