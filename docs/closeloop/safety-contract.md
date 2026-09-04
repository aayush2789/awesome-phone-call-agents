# CloseLoop Safety Contract

This document establishes the binding safety contract for **CloseLoop**, an outcome-driven phone-call orchestration runtime built on top of CALL-E.

Phone calls have real-world physical and social side effects. CloseLoop treats voice calling as an expensive, restricted input/output operation. Every call placed must be justified, authorized, verified, bounded, and audited.

---

## 1. The Twelve Safety Invariants

CloseLoop strictly enforces the following twelve safety invariants. A failure of any invariant fails closed immediately: the operation halts, no call is initiated, the ledger is updated, and an alert or review record is generated.

### 1. Explicit User Intent
- CloseLoop never places a call autonomously without an explicit, machine-validated workflow specification initiated by an authorized user or system.
- Workflows must define a specific business objective and structured outcome contract.

### 2. Verified Consent per Contact Rung
- Every contact rung in an escalation or cascade chain must specify a concrete, documented `consent_basis` string.
- Workflows lacking a declared consent basis for any target contact will be rejected during preflight validation before any side effect can occur.

### 3. Valid E.164 Phone Numbers
- All phone numbers must conform strictly to the international ITU-T E.164 standard (e.g. `^\+[1-9]\d{6,14}$`).
- Malformed numbers, local-only formats, extensions, or blank strings are rejected at preflight.
- Repository fixtures, examples, and automated tests must only use RFC-compliant fictional numbers (e.g., the `+1555010XXXX` range). Real phone numbers are never committed to version control.

### 4. Quiet Hours & Timezone Enforcement
- Calling hours are strictly restricted per contact or workflow.
- Quiet hours (e.g., 21:00 to 09:00) must be evaluated against the contact's explicitly declared IANA timezone (e.g. `Asia/Kolkata`, `America/New_York`).
- If a contact is currently inside their declared quiet-hour window, CloseLoop blocks the call and yields or reschedules rather than dialing.

### 5. Total Call Budget Caps
- Every workflow must declare a hard cap on total phone calls (`max_calls_total`).
- CloseLoop counts all historical and in-flight calls associated with the workflow run ID. If `in_flight + completed >= max_calls_total`, the entire chain halts with status `BLOCKED_BUDGET_EXHAUSTED`.

### 6. Rung-Level Attempt Limits
- Each contact rung specifies a `max_attempts` limit (typically 1 or 2).
- Once a rung's attempt budget is spent without achieving a terminal outcome, the engine advances to the next rung or closes the chain according to policy.

### 7. Suppression List Verification
- CloseLoop consults a persistent suppression registry before every planned call.
- Any phone number on the suppression list is immediately bypassed or terminated. No call may ever be placed to a suppressed number.

### 8. Hard Refusal Protocol
- When a callee expresses an explicit refusal to be contacted (e.g., "Do not call me again", "Remove my number"), CloseLoop classifies the outcome as `hard_refusal`.
- A `hard_refusal` outcome triggers two atomic actions:
  1. Immediately and permanently terminates the workflow chain.
  2. Enters the phone number into the persistent suppression registry.

### 9. Immediate Kill-Switch Mechanism
- CloseLoop supports an out-of-band kill switch via environment flag (`CLOSELOOP_KILL_SWITCH=1`) or file existence (`.closeloop_kill_switch`).
- When active, all in-flight planning and execution ceases immediately with a fail-closed status.

### 10. Cancellation & Rollback Semantics
- Workflows can be cancelled at any point prior to call initiation.
- Once a CALL-E plan is generated, it will not be executed if a cancellation request has been registered in the execution ledger.

### 11. Deterministic Idempotency & Crash Recovery
- Every execution attempt is assigned a deterministic execution key derived from `sha256(run_id + ":" + rung + ":" + attempt)`.
- The execution state is recorded in a transactional ledger before invoking any side-effecting operation.
- On process crash or restart, CloseLoop reconciles with the ledger. Terminal attempts are never redialed, and ambiguous states require manual reconciliation.

### 12. Privacy, Redaction & Credential Protection
- **Phone Masking**: All user-facing outputs, CLI summaries, logs, and public envelopes must mask phone numbers (e.g., `+1555010****`).
- **Credential Protection**: Authentication tokens, bearer tokens, API keys, cookies, login URLs, and confirmation tokens must never be written to standard output, log files, or persisted artifacts.

---

## 2. Sensitive Domain Boundaries

CloseLoop is an orchestration runtime, not an expert advisor or emergency service. Workflows operating in high-stakes domains must adhere to strict boundary rules:

| Domain | Permitted Coordination | Prohibited Behavior |
| :--- | :--- | :--- |
| **Medical / Healthcare** | Appointment reminder, logistical slot confirmation, clinic direction verification. | Medical diagnosis, treatment recommendations, triage advice, emergency dispatch. |
| **Financial** | Scheduling an advisor meeting, verifying document submission receipt. | Offering financial advice, requesting card numbers, PINs, or account credentials. |
| **Legal** | Scheduling consultation hearings, verifying logistical attendance. | Providing legal counsel, negotiating claims, taking binding legal depositions. |
| **Emergency** | Dispatching non-emergency maintenance scheduling. | Replacing 911/112 services, handling active life-safety incidents. |

---

## 3. Preflight & Pre-Execution Safety Flow

Before any call is executed, CloseLoop executes two distinct safety gates:

```text
[ Workflow Requested ]
         |
         v
[ Preflight Safety Gate ]
  - Verify Explicit Intent
  - Validate Workflow Spec & Schema
  - Check Kill Switch
  - Check Suppression List
  - Validate E.164 Number
  - Check Quiet Hours (Contact Timezone)
  - Check Total Call Budget
  - Check Rung Attempt Limit
  - Verify Consent Basis String
         |
         v
[ CALL-E Plan Call ]
         |
         v
[ Post-Planning Safety Gate ]  <--- MANDATORY CHECKPOINT
  - Inspect Planned Recipient vs Target Rung Phone
  - Inspect Rendered Goal for Sensitive Leaks
  - Inspect Plan Feasibility & Bounds
         |
         v
[ CALL-E Run Call ]
```

The Post-Planning Safety Gate ensures that CloseLoop never executes a plan without inspecting what CALL-E planned to say and do.
