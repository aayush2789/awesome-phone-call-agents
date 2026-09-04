# CloseLoop Architecture

## Overview

CloseLoop is an outcome-driven phone-call orchestration layer built on top of CALL-E.

The central thesis of the architecture is:
> **Every calling platform optimizes for calls placed. CloseLoop optimizes for outcomes closed, and the calls it does not make are the product.**

Rather than treating phone calling as an end in itself, CloseLoop views voice telephony as a high-cost, high-latency input/output mechanism. The engine determines:
1. Whether a call is legally and ethically permitted.
2. Which recipient or escalation rung to engage next.
3. How to plan the call with CALL-E and inspect that plan before dialing.
4. How to bound polling and parse structured results against machine-verifiable schemas.
5. How to route the workflow: close, retry, escalate, schedule, suppress, or request human review.

---

## Architectural Layers

The system comprises four decoupled layers:

```text
               +-------------------------------------------+
               |             Experience Layer              |
               |       CLI / Agent Runner / Web Console    |
               +-------------------------------------------+
                                     |
                                     v
               +-------------------------------------------+
               |        Workflow Orchestration Layer       |
               |      State Machine & Strategy Routing     |
               +-------------------------------------------+
                     |                             |
                     v                             v
   +---------------------------------+   +---------------------------------+
   |      Safety & Policy Layer      |   |       Evidence & Ledger         |
   | Quiet Hours, Budget, Kill Switch|   | Idempotency, SQLite Run Audit   |
   +---------------------------------+   +---------------------------------+
                     \                             /
                      \                           /
                       v                         v
               +-------------------------------------------+
               |          CALL-E Adapter Interface         |
               |       (FakeAdapter / CLI / MCP / API)     |
               +-------------------------------------------+
                                     |
                                     v
               +-------------------------------------------+
               |              CALL-E Runtime               |
               |           Telephony & Voice AI            |
               +-------------------------------------------+
```

### 1. Experience Layer
The user or orchestrating agent interacts with CloseLoop via a declarative YAML workflow specification. CLI commands like `python -m closeloop run spec.yaml --dry-run` provide predictable execution and verification.

### 2. Orchestration Layer
Operates as an explicit state machine:
- `INIT` -> `PREFLIGHT` -> `READY` -> `PLANNING` -> `PLAN_APPROVED` -> `RUNNING` -> `RESULT_RECEIVED` -> `RESULT_VALIDATED`
- Followed by terminal or continuation states: `CLOSED`, `RETRY`, `NEXT_RUNG`, `SCHEDULE`, `HUMAN_REVIEW`, `BLOCKED`, `TERMINATED`.
- Primary strategy: **Cascade** (sequentially evaluates contacts across rungs until an outcome condition is satisfied or resources are exhausted).

### 3. Safety & Policy Layer
Enforces the 12 Safety Invariants defined in [docs/closeloop/safety-contract.md](safety-contract.md). Provides strict preflight gating and post-planning inspection before any real-world call invocation.

### 4. Adapter Layer
Maintains complete provider separation:
- `CalleAdapterBase`: Abstract base class with methods `auth_status`, `tools_check`, `plan`, `inspect_plan`, `run`, `status`.
- `FakeAdapter`: Deterministic in-memory simulation with reproducible fixtures for offline development, CI/CD, and hackathon judging without spending live calls.
- `CalleCliAdapter`: Production adapter executing CALL-E via subprocess CLI commands, consuming structured JSON payloads while preserving security boundaries.
- `CalleMcpAdapter`: Fast tool integration via the standard MCP server tools.

---

## Idempotency & Persistence Model

CloseLoop uses a deterministic execution key for every calling attempt:
```text
key = sha256(run_id + ":" + rung_name + ":" + str(attempt_number))
```

All attempts are written to a transactional SQLite ledger with state tracking:
- `PLANNED`
- `EXECUTING`
- `TERMINAL`

If the process crashes or is interrupted, the ledger is inspected upon restart to prevent accidental duplicate calls.
