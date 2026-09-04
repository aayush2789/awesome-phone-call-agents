# CloseLoop Engineering Log (FEEDBACK.md)

This log records discoveries, environment verification, assumptions, and key engineering decisions during the development of CloseLoop.

---

## 2026-09-03: Phase 0 & Phase 1 — Environment Reconnaissance & Safety Contract

### Baseline Reconnaissance
1. **Branch Verification**:
   - Current branch: `feat/outcome-close-chain`.
   - Validated against `docs/git-naming-conventions.md` using `scripts/check_branch_name.py`.
2. **Repository Standards**:
   - Installable skills must live in `skills/<slug>/` with `SKILL.md` (no `README.md`) and `references/` containing at least `safety.md` and `examples.md`.
   - Runnable apps belong under `apps/<language>/<app-name>/` with `README.md` and dry-run capabilities.
   - Text must be English-only with zero non-English CJK characters.
   - Baseline repository validation via `python scripts/validate_repository.py` passed with code 0.
3. **CALL-E Environment**:
   - CALL-E CLI is installed at `C:\Users\aayuk\AppData\Roaming\npm\calle`.
   - `calle auth status --json` confirmed a usable cached token valid until 2029.
   - `calle mcp tools --json` confirmed presence of tools `plan_call`, `run_call`, and `get_call_run`.
4. **Python Runtimes**:
   - Python 3.12.3 with `pytest`, `pydantic`, `jsonschema`, and `pyyaml` installed and confirmed.

### Architectural Decisions (Phase 1)
- **Safety First**: Formalized the 12 Safety Invariants in `docs/closeloop/safety-contract.md`.
- **Fail-Closed Principle**: Any preflight failure, quiet-hours match, budget exhaustion, or missing consent halts execution immediately without side effects.
- **Mandatory Plan Inspection**: In CALL-E's lifecycle, planning returns a proposed script/goal. CloseLoop introduces a mandatory post-planning inspection checkpoint before calling `run`. Convenience operations that bypass planning or inspection are explicitly banned.
- **Strict Privacy**: All phone numbers in logs and output envelopes are masked (`+1555010****`), and sensitive tokens (tokens, cookies, auth URLs) are scrubbed.

### Validation Results (Phase 1)
- Unit tests: `pytest apps/python/closeloop/tests/test_safety_contract.py -v` -> 34 passed in 0.29s.
- Repository validation: `python scripts/validate_repository.py` -> Passed with code 0.

---

## 2026-09-04: Phase 2 — Typed Internal Data Model

### Architectural Decisions (Phase 2)
- **Provider-Independent Modeling**: Created Pydantic v2 domain models decoupling CALL-E runtime/CLI specifics from CloseLoop's business logic.
- **Fail-Closed Field Validation**: Validated E.164 formatting (`E164ValidationError`) and non-empty explicit consent basis (`ConsentMissingError`) directly at model instantiation in `ContactRung`.
- **Deterministic Idempotency**: Automated key generation `sha256(run_id:rung:attempt)` on `CallAttempt` to guarantee replay safety and crash recovery across restarts.
- **Mandatory Inspection State**: Encapsulated `CallPlan` with explicit approval/rejection state machines (`approved`, `rejection_reason`), enforcing that calls cannot proceed without inspection.
- **Standardized Result Envelope**: Implemented `WorkflowResult.to_envelope_dict()` conforming to Section 19 shared result envelope specification with automated recipient phone masking and ISO-8601 formatting.

### Models Implemented
- `QuietHoursConfig`, `OutcomeContract`, `ContactRung`, `Policy`, `StrategyConfig`, `WritebackConfig`, `WorkflowSpec`
- `CallPlan`, `CallRun`, `Evidence`, `OutcomeResult`, `AuditEntry`, `CallAttempt`, `ExecutionLedger`, `WorkflowResult`

### Validation Results (Phase 2)
- Unit tests: `pytest apps/python/closeloop/tests/ -v` -> 48 passed in 0.32s with 0 warnings.
- Repository validation: `python scripts/validate_repository.py` -> Passed with code 0.

---

## 2026-09-04: Phase 3 — Standalone Workflow Specification Validator

### Architectural Decisions (Phase 3)
- **Pre-Execution Gate**: Created a standalone validator in `closeloop/validator.py` executing before any CALL-E adapter or calling layer is reached, preventing invalid specifications from generating side effects.
- **Path-Indexed Error Attribution**: Every error explicitly indexes the offending JSON path (e.g. `ladder[1].consent_basis`, `outcome.quiet_hours.timezone`, `outcome.result_schema`), conforming to Section 33 of the CloseLoop master plan.
- **Deep Schema & Timezone Verification**: Checked JSON Schema validity via `jsonschema.Draft202012Validator.check_schema` and confirmed timezone existence via standard library `zoneinfo.ZoneInfo`.
- **Budget & Ladder Sanity Checks**: Verified non-empty ladders, enforced unique rung names, checked minimum call budgets (`policy.max_calls_total >= 1`), and emitted non-fatal warnings when ladder max attempts exceed policy limits.

### Components Implemented
- `ValidationErrorDetail`, `ValidationReport`, `WorkflowValidator`, `validate_workflow_spec`

### Validation Results (Phase 3)
- Unit tests: `pytest apps/python/closeloop/tests/ -v` -> 62 passed in 0.50s with 0 warnings.
- Repository validation: `python scripts/validate_repository.py` -> Passed with code 0.

---

## 2026-09-04: Phase 4 — Abstract CALL-E Adapter & Complete FakeAdapter

### Architectural Decisions (Phase 4)
- **Adapter Interface Abstraction**: Defined `CalleAdapterBase` establishing the standard CALL-E lifecycle: `auth_status`, `tools_check`, `plan`, `inspect_plan`, `run`, and `status`.
- **Zero-Cost Dry-Run Determinism**: Built `FakeAdapter` simulating CALL-E without live credentials, network traffic, or phone costs, enabling CI/CD tests and reproducible judging demonstrations.
- **Complete 11-Class Fixture Matrix**: Authored realistic fixtures for all 11 outcome classes: `confirmed`, `reschedule`, `declined`, `no_answer`, `voicemail`, `screening`, `wrong_person`, `callback_requested`, `hard_refusal`, `error`, and `ambiguous`.
- **Mandatory Inspection Gate (Invariant 4)**: Enforced fail-closed behavior in `FakeAdapter.run()`, throwing `SafetyViolationError` if `plan.approved` is `False`.
- **Bounded Polling**: Simulated asynchronous status polling transitions from `running` to terminal states across configurable poll step thresholds.

### Components Implemented
- `AuthStatus`, `ToolsStatus`, `PlanRequest`, `PlanInspectionResult`, `CallStatus`, `CalleAdapterBase`, `FakeAdapter`, `ALL_OUTCOME_CLASSES`

### Validation Results (Phase 4)
- Unit tests: `pytest apps/python/closeloop/tests/ -v` -> 80 passed in 0.51s with 0 warnings.
- Repository validation: `python scripts/validate_repository.py` -> Passed with code 0.

---

## 2026-09-04: Phase 5 — Safety Engine (Preflight & Post-Plan Inspection Gate)

### Architectural Decisions (Phase 5)
- **Fail-Closed Preflight Gating**: Implemented `SafetyEngine.preflight_check` and `assert_preflight` sequentially evaluating kill switch, suppression list, explicit consent, E.164 format, quiet hours in target timezones, call budget limits, and idempotency collisions before authorization.
- **Mandatory Post-Plan Inspection (Invariants 4 & 11)**: Implemented `SafetyEngine.inspect_plan` and `assert_plan_inspection` ensuring the generated CALL-E `CallPlan` matches the target phone, matches the intended rung, and contains no prohibited clinical, legal, financial, or emergency advice.
- **Dynamic Suppression Registry**: Supported immediate number suppression upon explicit refusal or opt-out events, preventing subsequent calls across any rung.
- **Specific Error Hierarchy**: Created specialized `BudgetExhaustedError`, `IdempotencyCollisionError`, `PlanInspectionError`, and `RecipientMismatchError` deriving from `SafetyViolationError`.

### Components Implemented
- `PreflightCheckResult`, `PostPlanInspectionResult`, `SafetyEngine`, `BudgetExhaustedError`, `IdempotencyCollisionError`, `PlanInspectionError`, `RecipientMismatchError`

### Validation Results (Phase 5)
- Unit tests: `pytest apps/python/closeloop/tests/ -v` -> 90 passed in 0.53s with 0 warnings.
- Repository validation: `python scripts/validate_repository.py` -> Passed with code 0.

---

## 2026-09-04: Phase 6 — Orchestration Engine (State Machine & Cascade Strategy)

### Architectural Decisions (Phase 6)
- **Explicit State Machine**: Structured lifecycle states (`INIT` -> `PREFLIGHT` -> `READY` -> `PLANNING` -> `PLAN_APPROVED` -> `RUNNING` -> `RESULT_RECEIVED` -> `RESULT_VALIDATED` -> `[CLOSED | RETRY | NEXT_RUNG | SCHEDULE | HUMAN_REVIEW | BLOCKED | TERMINATED]`) replacing ad-hoc branching.
- **Cascade Strategy Implementation**: Iterates sequentially through contact rungs, checking attempt limits, budget restrictions, and routing fallback policies (`on_voicemail`, `on_wrong_person`, `on_hard_refusal`).
- **Dynamic Outcome Evaluation**: Evaluated declarative `stop_when` rules against JSON schema-validated structured results to determine objective closure.
- **Calls Avoided Optimization**: Accurately computes `calls_avoided = max_possible_calls - calls_placed`, mathematically proving efficiency gains when an outcome closes early in the ladder.
- **Export Writeback**: Seamlessly exports envelope results to CSV or JSON formats when configured.

### Components Implemented
- `WorkflowState`, `OrchestrationEngine`, `evaluate_stop_condition`, `execute_workflow`

### Validation Results (Phase 6)
- Unit tests: `pytest apps/python/closeloop/tests/ -v` -> 97 passed in 0.68s with 0 warnings.
- Repository validation: `python scripts/validate_repository.py` -> Passed with code 0.

---

## 2026-09-04: Phase 7 — Outcome Classification & Data-Driven Routing

### Architectural Decisions (Phase 7)
- **11 Normalized Outcome Classes**: Built `OutcomeClassifier` mapping raw adapter output, call statuses, and fixture fields to the standard CloseLoop 11-outcome ontology: `confirmed`, `reschedule`, `declined`, `no_answer`, `voicemail`, `screening`, `wrong_person`, `callback_requested`, `hard_refusal`, `ambiguous`, and `error`.
- **Deterministic Rule-Based Normalization**: Zero LLM dependency. Evaluates structured result payloads, telephony flags (`answered`, `beep_detected`, `gatekeeper_action`), and semantic decision keys deterministically.
- **Fail-Safe Confidence Thresholding**: Automatically demotes any outcome with confidence < 0.60 or contradictory signals to `ambiguous`, ensuring ambiguous/unverifiable claims never satisfy stop conditions.
- **JSON Schema Validation**: Validates structured results against the workflow contract's `result_schema` using `jsonschema`, recording detailed validation errors.
- **Decoupled Routing Engine**: Implemented `RoutingEngine` determining deterministic next actions (`close`, `next_rung`, `retry_same_rung`, `schedule_retry`, `human_review`, `stop_chain`, `fail_closed`) based on policy rules, budget status, and ladder position.

### Components Implemented
- `OutcomeClassifier`, `RoutingEngine`, `RoutingAction`, `RoutingDecision`

---

## 2026-09-04: Phase 8 — Persistent Idempotency Ledger (SQLite Implementation)

### Architectural Decisions (Phase 8)
- **Ledger Repository Abstraction**: Defined `LedgerRepositoryBase` interface specifying abstract contracts for workflow specs, attempt lifecycles, state transitions, audit trails, and crash recovery, ensuring engine storage engine swappability.
- **Database-Enforced Idempotency**: Built `SQLiteLedger` with WAL journaling, foreign keys, and unique constraints on `idempotency_key = sha256(run_id:rung:attempt)`. Duplicate insertions raise `DuplicateAttemptError` (derived from `SafetyViolationError`), guaranteeing the zero-duplicate-calls invariant.
- **Transactional State Transitions**: Supported explicit transitions (`planned` -> `executing` -> `terminal`) with atomic updates for call runs, classified outcomes, and execution errors.
- **Crash Recovery & Reconciliation**: Implemented `reconcile_in_flight_attempts(run_id)` detecting attempts stranded in `executing` status across process restarts and marking them as `needs_reconciliation` rather than blindly redialing.
- **Complete Audit Trail & Envelope Persistence**: Persisted full chronological audit trails and finalized standard outcome result envelopes.

### Components Implemented
- `LedgerRepositoryBase`, `SQLiteLedger`, `DuplicateAttemptError`

### Validation Results (Phase 7 & 8)
- Unit tests: `pytest apps/python/closeloop/tests/ -v` -> 126 passed in 0.63s with 0 warnings.
- Repository validation: `python scripts/validate_repository.py` -> Passed with code 0.

