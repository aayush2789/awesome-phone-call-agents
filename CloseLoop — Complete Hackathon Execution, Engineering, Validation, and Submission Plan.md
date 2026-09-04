# CloseLoop — Complete Hackathon Execution, Engineering, Validation, and Submission Plan

## 1. Executive Definition

CloseLoop is an outcome-orchestration runtime for CALL-E.

The central thesis is:

> **Every calling platform optimizes for calls placed. CloseLoop optimizes for outcomes closed, and the calls it does not make are the product.**

CloseLoop is not a conventional AI voice bot, a bulk dialer, or another appointment-confirmation application. The system takes a real-world objective, defines what a successful outcome means in a machine-verifiable contract, chooses an appropriate calling strategy, executes calls through CALL-E, interprets the resulting structured information, and continues, stops, escalates, schedules, or requests human intervention according to a deterministic policy.

The phone call is therefore treated as an input/output mechanism for an orchestration system rather than the product itself.

A CloseLoop workflow begins with an outcome such as confirming an interview slot. The system then receives an outcome contract, a set of consent-checked possible contacts, a routing policy, a call budget, timing restrictions, and a stop rule. CloseLoop executes the minimum number of CALL-E calls necessary to reach a defensible terminal outcome. When the result is insufficient, the system selects the next appropriate action rather than blindly retrying.

This architecture is intentionally broader than the original placement-confirmation demonstration. The placement workflow is the flagship demonstration because it provides a concrete real-world problem, but the underlying engine is designed around reusable orchestration primitives that can represent cascade, escalation, quorum, verification, scheduled follow-up, human handoff, and structured-outcome workflows.

The original source plan identifies the same direction by explicitly framing CloseLoop as the engine behind multiple phone workflows rather than as a single domain-specific bot.

---

# 2. What the Hackathon Is Actually Asking Us to Build

The hackathon should be treated as two connected submissions rather than a single artifact.

The first artifact is the actual functional project. It needs to use CALL-E meaningfully at runtime. The most important evidence is not that CALL-E is mentioned in the README, but that the submitted system actually invokes CALL-E and performs the `plan → inspect → run → status` lifecycle.

The second artifact is the contribution to `CALLE-AI/awesome-phone-call-agents`. This contribution needs to follow the repository's structure, conventions, validation requirements, safety requirements, and contribution scope.

The Devpost entry is the presentation and judging surface for that implementation. It should connect the problem, the architecture, the working implementation, the real-world evidence, the public demo, and the GitHub contribution into one coherent story.

The four judging dimensions should be treated as four separate engineering requirements.

Real-world impact means that we must demonstrate a specific operational problem experienced by a real user and measure it before and after CloseLoop.

Quality of the idea means that CloseLoop must be more than an obvious voice-agent wrapper. Its contribution should be reusable and explainable as a new orchestration abstraction rather than a single hard-coded workflow.

Technical implementation means that CALL-E must be part of the actual runtime path. The system must expose a genuine lifecycle around CALL-E rather than a fake simulation disguised as an integration.

Product experience and demo quality mean that someone unfamiliar with the project should understand what is happening, why it matters, and what CloseLoop does differently within approximately one minute of the demonstration beginning.

The original planning document translates these criteria into concrete evidence: a named pilot user, before/after measurements, the CALL-E lifecycle, structured results, idempotency, a dry-run system, and a real call appearing early in the video.

---

# 3. The Problem We Will Anchor the Demonstration Around

The flagship problem is confirmation-chain coordination.

The initial domain is campus placement and interview-slot coordination because it gives us a concrete situation in which a human is responsible for confirming a set of people, the outcome is objectively structured, the task is personalized rather than mass marketing, and failure to close an outcome has operational consequences.

The human process is usually something like this.

A coordinator has a set of candidates and interview slots. Some candidates confirm through existing channels. Others do not respond. The remaining candidates are manually called. Some answer immediately. Some request another time. Some do not answer. Some have an alternate contact number. Some may have a mentor, coordinator, or other approved escalation contact. The coordinator must repeatedly chase the unresolved rows until each slot is either confirmed, declined, rescheduled, or marked unreachable.

The actual output is not the conversation.

The actual output is a structured operational state.

The system needs to know whether the candidate is confirmed, needs rescheduling, has declined, or remains unreachable. It also needs a timestamp, a reason where appropriate, and an audit trail showing how the result was reached.

This makes the problem unusually well suited to CALL-E because the value is in a low-frequency, personalized phone interaction that generates a structured result rather than a bulk outbound campaign. CALL-E explicitly positions itself around goal-driven tasks and structured results for low-frequency personalized calling, which is precisely the environment in which this architecture makes sense.

The demonstration should not make unsupported claims about how many hours a coordinator saves. We will measure the actual process.

Before CloseLoop is used, we will record the time required to process a defined number of rows manually, the number of calls attempted, the number of outcomes successfully closed, and the amount of coordinator involvement.

After CloseLoop is used on the same class of task, we will record the corresponding measurements.

The central metric is not simply call duration.

The central metric is:

> **Outcomes closed per call placed.**

The supporting efficiency metric is:

> **Calls avoided because the outcome was closed earlier in the escalation chain.**

---

# 4. The Strategic Positioning of CloseLoop

The entire project should be positioned around a single conceptual distinction.

A conventional voice automation platform asks:

> How many calls can we automate?

CloseLoop asks:

> What is the smallest number of calls required to establish that the desired outcome is actually complete?

This framing produces four important ideas.

The first is the outcome contract.

The system does not decide that it is finished merely because a conversation "felt successful." Completion requires a structured result satisfying a defined schema and a defined stop condition.

The second is adaptive escalation.

The system does not call every available contact. It calls an ordered set of possible contacts and only moves further when the previous action did not establish the required outcome.

The third is outcome-class routing.

No answer, voicemail, callback request, wrong person, refusal, screening, ambiguous result, and transport error are not equivalent failures. Each represents a different operational state and therefore deserves a different next action.

The fourth is verifiable closure.

The engine records not just the final result but why the chain stopped, where it stopped, how many calls were placed, how many calls were avoided, and what evidence supports the terminal state.

The original plan explicitly identifies outcome contracts, ladder early exit, outcome-class routing, and bilingual rung switching as the central mechanisms that differentiate the system from a simple phone bot.

---

# 5. The Core Product Architecture

The system should have four primary layers.

The first layer is the experience layer.

This is the interface through which a user defines or triggers a workflow. The initial implementation can be CLI-first because the repository and CALL-E already provide strong CLI workflows. A lightweight web console can later sit on top of the same runtime without changing the core engine.

The second layer is the workflow orchestration layer.

This is CloseLoop itself. It interprets the workflow specification, applies policies, chooses a strategy, tracks execution state, calls the appropriate adapter, validates results, and determines whether to terminate or continue.

The third layer is the safety and policy layer.

This layer enforces consent, call budgets, quiet hours, number validity, idempotency, suppression lists, refusal handling, kill-switch behavior, and any domain-specific boundaries.

The fourth layer is CALL-E.

CALL-E remains responsible for real-world voice execution. CloseLoop should not attempt to become another telephone provider.

Conceptually the architecture is:

```text
                           CLOSELOOP
                              |
              +---------------+---------------+
              |               |               |
          Workflow         Policy           Evidence
         Orchestrator       Engine            Engine
              |               |               |
              +---------------+---------------+
                              |
                       CALL-E Adapter
                              |
               +--------------+--------------+
               |              |              |
              CLI            MCP            API
               |              |              |
               +--------------+--------------+
                              |
                           CALL-E
                              |
                           Phone
```

The implementation should use the stable CLI and MCP routes as the primary execution mechanisms, while treating the preview SDK and API as optional adapters. The source plan explicitly recommends CLI plus MCP as the reliable primary path because the SDK and API were marked as preview/development surfaces.

---

# 6. CALL-E Integration Design

The primary integration contract inside CloseLoop should be:

```text
auth_status()
tools_check()
plan(...)
inspect_plan(...)
run(...)
status(...)
```

The first CALL-E interaction is authentication verification.

The second is a capabilities check to ensure that the expected CALL-E tools exist.

The third is call planning.

The fourth is plan inspection.

The fifth is execution.

The sixth is bounded status polling.

The seventh is result extraction and validation.

This flow must not be collapsed into a convenience "start call" operation because CloseLoop needs a visible safety checkpoint between planning and execution.

The source plan specifically calls out `calle auth status`, `calle mcp tools`, `calle call plan`, `calle call run`, and `calle call status` as the important runtime call sites and explicitly rejects `calle call start` for the main engine because it hides the confirmation step.

The CLI adapter should be implemented first because successful CLI output is JSON and can therefore be parsed by a Python engine through a subprocess boundary.

The adapter should never log authentication tokens, confirmation tokens, cookies, login URLs, or other sensitive credentials.

The adapter interface should be independent from the engine so that the FakeAdapter can behave exactly like the real CALL-E adapter from the engine's perspective.

The interface should conceptually look like:

```python
class CalleAdapter:
    def auth_status(self) -> AuthStatus: ...
    def tools_check(self) -> ToolsStatus: ...
    def plan(self, request: PlanRequest) -> CallPlan: ...
    def run(self, plan: CallPlan) -> CallRun: ...
    def status(self, run_id: str, cursor: str | None = None) -> CallStatus: ...
```

The engine should not know whether the implementation underneath it is CLI, MCP, API, or Fake.

---

# 7. The Workflow Specification

The public interface of CloseLoop should be declarative.

The user should describe the desired outcome rather than implement their own orchestration code.

The initial specification format should be YAML.

A simplified example is:

```yaml
run_id: placement-2026-slot-114

outcome:
  name: interview_slot_confirmation
  deadline: 2026-09-04T18:00:00+05:30

  quiet_hours:
    start: "21:00"
    end: "09:00"
    timezone: Asia/Kolkata

  result_schema:
    type: object
    required:
      - decision
    properties:
      decision:
        type: string
        enum:
          - confirmed
          - reschedule
          - declined
          - unreachable
      preferred_slot:
        type: string
      reason:
        type: string
    additionalProperties: false

  stop_when:
    expression: "decision in [confirmed, declined]"

strategy:
  type: cascade

ladder:
  - rung: candidate
    phone: "+15550101234"
    region: IN
    language: English
    consent_basis: "candidate opted in during placement registration"
    max_attempts: 2

  - rung: alternate_number
    phone: "+15550101235"
    region: IN
    language: English
    consent_basis: "alternate number explicitly supplied by candidate"
    max_attempts: 1

  - rung: mentor
    phone: "+15550101236"
    region: IN
    language: Hindi
    consent_basis: "authorized coordinator contact"
    max_attempts: 1

policy:
  max_calls_total: 4
  on_voicemail: next_rung
  on_callback_requested: schedule_retry
  on_wrong_person: transfer_then_next
  on_hard_refusal: stop_chain

writeback:
  target: csv
  path: ./out/results.csv
```

This specification should remain small enough that someone can understand it without opening the source code.

The original proposal already defines the main outcome, ladder, policy, quiet-hours, consent, call budget, and writeback fields, and that structure should remain the foundation of the implementation.

---

# 8. Expanding the Architecture Beyond One Ladder

This is the most important architectural addition.

The current `awesome-phone-call-agents` repository already contains workflows representing different patterns. Because of that, we should not present "contact ladder" as the entirety of our innovation.

CloseLoop should instead define a small set of orchestration strategies.

The `single` strategy performs exactly one call.

The `cascade` strategy calls contacts in ordered sequence until a terminal outcome is established.

The `escalation` strategy calls progressively more authoritative contacts when the previous contact cannot acknowledge or resolve the issue.

The `quorum` strategy continues until a required number of confirmations have been obtained.

The `parallel` strategy can launch multiple independent calls under explicit policy constraints when doing so is safe and useful.

The `verify` strategy is designed to establish ground truth rather than obtain a commitment.

The `schedule` strategy records a future callback requirement but leaves scheduling to a host scheduler.

The `human_review` strategy terminates the automated chain and emits a structured request for human intervention.

These strategies should be configuration rather than separately hard-coded applications.

That is how we turn the patterns already represented by workflows such as standby, incident escalation, mobilization, structured-outcome follow-up, verification, and candidate availability into a common orchestration model.

The goal is not to copy those Skills. The goal is to implement a runtime abstraction that can express their common execution patterns.

---

# 9. The Outcome Model

CloseLoop's internal state model should distinguish at least three levels.

The first level is the raw CALL-E execution state.

Examples are planned, running, completed, failed, cancelled, or timed out.

The second level is the interpreted voice outcome.

Examples are confirmed, no answer, voicemail, callback requested, wrong person, screening, refusal, partial, ambiguous, or error.

The third level is the workflow state.

The workflow state should ultimately be one of:

```text
closed
not_closed
blocked
human_review
```

This distinction is critical because a raw call completion is not the same thing as outcome closure.

For example:

```text
CALL-E:
completed

CloseLoop interpretation:
callback_requested

Workflow:
not_closed
```

Another example is:

```text
CALL-E:
completed

CloseLoop interpretation:
confirmed

Workflow:
closed
```

The source plan explicitly separates the call result from the outcome class and from final chain termination.

---

# 10. Outcome-Class Routing

The routing engine is one of the core technical components.

A confirmed structured result should terminate the chain.

A partial result should generally re-plan the same rung or move into a defined follow-up action.

A no-answer result should permit a bounded retry before escalating.

A voicemail should be handled carefully and should not result in endless repetition.

A screening or gatekeeper condition should receive a purpose-specific action rather than a generic retry.

A wrong person should cause a controlled transfer attempt or advancement to the next authorized rung.

A callback request should become a scheduled action if it is still within the workflow deadline and permitted calling hours.

A hard refusal should terminate the chain permanently and create a suppression record.

A transport, authentication, or timeout error should fail closed rather than automatically creating another side effect.

This is not only an implementation detail. It is one of the best demonstrations that the system understands real-world phone behavior instead of treating every call as a binary success/failure event.

The original source defines this routing table and specifically identifies hard refusal as a terminal state with persistent suppression and error as fail-closed behavior.

---

# 11. The Outcome Contract and Schema Validation

CloseLoop must treat the JSON schema as the definition of completion.

Suppose the call returns:

```json
{
  "decision": "confirmed"
}
```

That is valid.

Suppose the call returns:

```json
{
  "message": "The candidate sounded positive."
}
```

That is not a valid terminal result for a workflow whose required field is `decision`.

Suppose the call returns:

```json
{
  "decision": "reschedule"
}
```

This may be a valid structured result but still fail the stop condition.

This creates a useful distinction:

```text
schema validity != workflow completion
```

Schema validity asks:

> Did we get the expected type of answer?

The stop condition asks:

> Does the answer establish the actual business outcome?

This is one of the strongest concepts in the project.

The API preview capabilities described in the source explicitly include `result_schema`, `structuredResult`, and `resultValidation`, and the project should use these concepts where the API adapter is available.

---

# 12. Evidence and Confidence Layer

The project should add an evidence layer to the original design.

A structured result should not only contain:

```json
{
  "decision": "confirmed"
}
```

It should optionally contain:

```json
{
  "decision": "confirmed",
  "confidence": 0.94,
  "evidence": {
    "source": "transcript",
    "speaker": "callee",
    "excerpt_hash": "..."
  }
}
```

The actual transcript may not always be shown in the user-facing result because privacy controls should govern what is stored or displayed.

The important principle is that the system should preserve enough structured provenance to explain why the final result was accepted.

This allows CloseLoop to support verification-style workflows and makes the distinction between "the model generated a result" and "the system has evidence supporting the result" much clearer.

The current repository's verification-oriented Skills already emphasize structured evidence and abstention rather than blindly asserting a result, which makes evidence a natural extension of the orchestration architecture.

---

# 13. Safety Architecture

Safety must be an architectural layer rather than a README paragraph.

Every workflow should pass a preflight stage before execution.

The preflight should verify the existence of an explicit user-intended workflow.

Each rung must contain a concrete consent basis.

Each phone number must be valid E.164.

The quiet-hours policy must be enforced using the rung's declared timezone.

The total call budget must be checked before every plan operation.

A kill switch must be checked before every actual call.

A suppression list must be consulted before any call.

The plan returned from CALL-E must be checked against the intended recipient.

The rendered goal must be checked before execution.

Sensitive credentials and confirmation tokens must never enter logs.

Phone numbers shown in user-facing summaries should be masked.

Hard refusal must permanently terminate the chain and create a suppression record.

The system must provide explicit cancellation instructions.

Medical, financial, legal, and emergency workflows must remain constrained to logistics and must never be used for professional advice or emergency response.

Repository examples must only contain fictional phone numbers.

The source plan provides this safety contract in explicit detail and states that it is a prerequisite rather than an optional documentation layer.

---

# 14. Idempotency and Crash Recovery

Idempotency is one of the areas where CloseLoop should behave like production software rather than a hackathon script.

Every intended execution attempt should have a deterministic identity.

The initial implementation should use:

```text
sha256(run_id + rung + attempt)
```

as the execution key.

The ledger should be persisted before the potentially side-effecting execution starts.

The record should transition through states such as:

```text
planned
executing
terminal
```

If the application crashes while processing a workflow, the next invocation should inspect the ledger before making any new call.

If an attempt is already terminal, the system must not redial it.

If an attempt is ambiguous because execution status could not be confirmed, the default should be to require reconciliation rather than blindly placing a duplicate call.

This is one of the most important production-grade properties in the entire system because a crash followed by an unintended duplicate call is a real-world side effect.

The source design explicitly proposes the same deterministic idempotency key strategy and persistent ledger behavior.

---

# 15. The FakeAdapter and Dry-Run System

The dry-run mode should be the default.

A judge should be able to clone the contribution and demonstrate the entire orchestration process without:

```text
CALL-E credentials
real phone numbers
real calls
```

The FakeAdapter should simulate the same interface as the CALL-E adapter.

Fixtures should model realistic `get_call_run` responses.

The initial fixture matrix should include:

```text
confirmed
reschedule
declined
no_answer
voicemail
screening
wrong_person
callback_requested
hard_refusal
error
ambiguous
```

The dry-run engine should produce the full audit trail and final result envelope exactly as the real engine would.

For example:

```text
$ python -m closeloop run examples/placement.yaml --dry-run

Workflow: placement-2026-slot-114
Strategy: cascade

Attempt 1
Rung: candidate
Outcome: confirmed

Schema validation: PASS
Stop condition: PASS

Workflow status: CLOSED
Calls placed: 1
Calls avoided: 2
Closed on rung: candidate
```

The source roadmap specifically encourages dry-run modes, fake servers, preview payloads, and fictional numbers because they make artifacts easier for maintainers and judges to test.

---

# 16. The Test Architecture

Tests should be organized around behavior rather than implementation details.

The first category is specification validation.

The system must reject malformed YAML, missing outcome contracts, invalid schemas, invalid timezones, missing consent bases, malformed E.164 values, duplicate rung identifiers, invalid policies, and illegal strategy combinations.

The second category is safety validation.

The tests must prove that quiet hours work, budgets are enforced, hard refusals stop the chain, suppression blocks later calls, kill-switch behavior prevents execution, and phone numbers are masked in user-facing outputs.

The third category is orchestration validation.

The tests must prove that a confirmed result stops the cascade, an unresolved result moves to the next rung, a reschedule result keeps the workflow alive, and a callback request creates a bounded retry plan.

The fourth category is idempotency validation.

A terminal ledger entry must prevent another call.

A crash simulation must not lead to an unintended duplicate call.

The fifth category is result validation.

Schema failures must not be interpreted as successful closure.

The sixth category is security validation.

No secret, token, login URL, or cookie should appear in stdout or logs.

The seventh category is strategy validation.

Cascade, escalation, quorum, verification, single-call, and human-review behavior should each have fixtures.

The source plan already specifies offline tests for spec validation, quiet hours, the call budget, idempotency replay, early exit, refusal stop, schema failures, masking, and secret leakage; these should form the baseline suite.

---

# 17. Repository Structure

The primary GitHub contribution should be organized according to the repository's existing house style.

The contribution should initially look like:

```text
skills/outcome-close-chain/
    SKILL.md
    references/
        architecture.md
        strategy-types.md
        outcome-classes.md
        spec-format.md
        result-envelope.md
        calle-routes.md
        safety.md
        testing.md
    scripts/
        validate-spec.mjs
        render-call-goal.mjs
        dry-run.mjs
    assets/
        example-spec.yaml
        fixtures/
            confirmed.json
            reschedule.json
            declined.json
            no-answer.json
            voicemail.json
            screening.json
            wrong-person.json
            callback-requested.json
            hard-refusal.json
            error.json
            ambiguous.json
```

A companion runnable application can then live separately:

```text
apps/python/closeloop-runner/
    README.md
    closeloop/
        engine.py
        models.py
        policy.py
        router.py
        adapters/
            base.py
            cli.py
            mcp.py
            api.py
            fake.py
        ledger.py
        evidence.py
        writeback.py
        safety.py
    tests/
    examples/
```

The primary repository contribution should remain scoped.

The application should not make the Skill impossible to understand.

The original plan recommends exactly this separation, with the Skill as the primary contribution and an optional runnable app as a companion contribution.

---

# 18. What the SKILL.md Must Do

`SKILL.md` is not the entire implementation.

It should be the progressive-disclosure entry point.

The frontmatter should provide the skill name, matching directory name, description, and license.

The description should contain the trigger language that makes it discoverable by an agent host.

The body should explain when the Skill should be used, when it should not be used, the creation-time workflow, runtime workflow, safety requirements, and output format.

The description should communicate that the Skill closes defined outcomes through a consent-checked contact strategy and terminates when the required structured result is established.

The source plan explicitly recommends mirroring the existing house style, including the `When To Use`, `When Not To Use`, `Core Create-Time Workflow`, `Runtime Workflow`, `Safety Rules`, and `Output Format` sections.

---

# 19. The Shared Result Envelope

CloseLoop should emit a common result envelope.

A representative result is:

```json
{
  "run_id": "placement-2026-slot-114",
  "status": "closed",
  "outcome": "confirmed",
  "summary": "Candidate confirmed the interview slot.",
  "structured_result": {
    "decision": "confirmed",
    "preferred_slot": "2026-09-04T15:00+05:30"
  },
  "result_validation": {
    "valid": true
  },
  "closed_on_rung": "candidate",
  "attempt_index": 1,
  "calls_placed": 1,
  "calls_avoided": 2,
  "external_call_id": "run_123",
  "recipient_phone_e164": "+1555010****",
  "started_at": "2026-09-02T11:04:12+05:30",
  "completed_at": "2026-09-02T11:06:48+05:30",
  "source_platform": "closeloop",
  "source_object_id": "slot-114",
  "transcript_url": null,
  "recording_url": null,
  "audit": [
    {
      "rung": "candidate",
      "attempt": 1,
      "outcome_class": "closed"
    }
  ]
}
```

The envelope should deliberately align with the repository roadmap's proposed common fields while adding CloseLoop-specific fields such as `calls_avoided`, `closed_on_rung`, and `result_validation`.

The source plan explicitly identifies this as a way to answer an open repository question around shared result fields.

---

# 20. The Roadmap and Repository Contribution Strategy

The repository already contains multiple examples covering calls, structured outcomes, confirmation, escalation, verification, human handoff, sourcing, approvals, and monitoring.

Therefore, CloseLoop should not claim novelty merely because it performs one of those tasks.

The contribution should instead claim novelty at the orchestration level.

The repository's existing workflows become the evidence that there is a real family of reusable patterns.

CloseLoop provides a common execution model for those patterns.

A future CloseLoop workflow should be able to express a concept such as:

```yaml
strategy:
  type: cascade
```

or:

```yaml
strategy:
  type: quorum
  required_confirmations: 3
```

or:

```yaml
strategy:
  type: verify
```

or:

```yaml
strategy:
  type: escalation
```

The important point is that the domain-specific Skill becomes a configuration of the orchestration engine rather than a separate runtime implementation.

This is how we turn the repository's breadth into evidence for our architecture rather than competing with every existing Skill individually.

---

# 21. Plugin Strategy

The current repository contains integrations such as n8n, Dify, Zapier, and HubSpot.

We should not attempt to recreate all of those integrations as core CloseLoop code during the first implementation stage.

The correct architecture is to define a generic event-source and event-sink interface.

A source provides workflow input.

CloseLoop executes the workflow.

A sink receives the structured result.

This gives us:

```text
source
   |
   v
CloseLoop
   |
   v
CALL-E
   |
   v
result
   |
   v
sink
```

The first implementation should use local YAML and CSV because they are deterministic and easy to test.

A later integration layer can expose:

```text
CSV
Google Forms
Airtable
Notion
n8n
Dify
Zapier
HubSpot
```

The development order should prioritize proving the orchestration architecture over proving that every SaaS platform can be connected.

If time remains, the strongest plugin demonstration is an n8n integration because it naturally illustrates:

```text
event
→ workflow
→ CloseLoop
→ CALL-E
→ structured result
→ downstream automation
```

---

# 22. The Web Console

The dashboard is optional.

It should not delay the core engine.

If built, its purpose should be to visualize the orchestration process rather than become another generic admin dashboard.

The central screen should show:

```text
Workflow
Outcome Contract
Current Strategy
Current Rung
Call Status
Outcome Class
Schema Validation
Decision
Calls Placed
Calls Avoided
Audit Trail
```

A compelling visual state could be:

```text
Candidate
   |
   | confirmed
   v
[CLOSED]

Calls placed: 1
Calls avoided: 2
```

The dashboard should also be able to show a failed chain:

```text
Candidate
   |
   | no_answer
   v
Retry
   |
   | voicemail
   v
Mentor
   |
   | hard_refusal
   v
[CHAIN STOPPED]
```

This gives judges an immediate visual representation of why CloseLoop is different from a normal phone bot.

---

# 23. The Real CALL-E Demonstration

The first live demonstration should use the CLI path because it is the most predictable.

The demo should visibly perform:

```text
calle auth status
calle mcp tools
calle call plan ...
calle call run ...
calle call status ...
```

The plan must be inspected before execution.

The recipient should be matched against the configured number.

The goal should be matched against the rendered goal.

The confirmation token must never appear in the recording or logs.

The terminal should show the resulting structured outcome.

The source plan explicitly lists the runtime CALL-E operations that should be visible as evidence in the video.

---

# 24. Bilingual Demonstration

Because India supports English and Hindi in the documented CALL-E region/language configuration, the system should support per-rung language selection.

The first rung can use English.

A later escalation rung can use Hindi.

The workflow definition should therefore allow:

```yaml
language: English
```

and:

```yaml
language: Hindi
```

independently for different rungs.

This is particularly useful in the Indian placement coordination scenario because it is a meaningful localization behavior rather than a decorative feature.

The original plan specifically identifies bilingual rung switching as one of the differentiators available for an India-based demonstration.

The Hindi demonstration should be included only after the English call path is stable.

---

# 25. The Live Pilot

The live pilot must be treated as an experiment, not as marketing material.

We need one concrete organization or operational team.

The best initial target is placement coordination at the institute level because access is likely easier than convincing an unrelated commercial organization to participate in an experimental call workflow.

We will first observe the manual process.

The measurement should record a fixed sample, ideally ten rows.

For each row we should record:

```text
manual minutes
number of manual calls
final outcome
```

Then we run the equivalent workflow through CloseLoop and record:

```text
CloseLoop minutes requiring coordinator intervention
calls placed
calls avoided
final outcome
closed on rung
```

The key comparison becomes:

```text
Manual:
X minutes
Y calls
Z closed outcomes

CloseLoop:
A minutes
B calls
C calls avoided
D closed outcomes
```

The original plan explicitly recommends a before/after measurement with ten rows rather than a large unsupported market-size claim.

---

# 26. Consent for the Pilot

No real phone number should enter the GitHub repository.

Real pilot data must remain outside the public contribution.

Every real participant should have a documented basis for receiving the call.

The system should make the consent basis explicit in the workflow configuration.

The demonstration can show a masked consent record without showing personal numbers.

This is important not only for safety but also for credibility. The repository's contribution rules emphasize explicit user intent, E.164 numbers, masking, no credential exposure, no hidden recurring schedules, no duplicate jobs, cancellation, and boundaries around sensitive domains.

---

# 27. Call Budget Management

The original plan assumes twenty CALL-E calls.

We should treat this as a hard resource constraint even if more calls are granted later.

Development should happen almost entirely through FakeAdapter.

The initial real-call allocation should be roughly:

```text
3 calls for smoke testing
4 calls for outcome-state capture
6 calls for pilot
4 calls for demo recording
3 calls as reserve
```

This is the exact resource model proposed in the source plan.

We should request any additional available CALL-E allocation as early as possible, but the architecture must remain fully viable if the request is denied.

---

# 28. Development Workflow

The correct development order is not:

```text
UI
then AI
then API
then safety
```

The correct development order is:

```text
Safety contract
→ specification
→ state model
→ fake runtime
→ orchestration engine
→ tests
→ real CALL-E adapter
→ live pilot
→ presentation
```

This prevents us from spending the precious real-call budget while debugging logic that could have been tested offline.

---

# 29. Phase 0 — Repository and Environment Setup

The first development session should create the working environment.

Clone or fork the repository.

Create the feature branch using the repository's branch naming conventions.

Install the CALL-E CLI.

Authenticate.

Run the CALL-E tools capability check.

Clone the project repository locally.

Run the repository's baseline validation before making changes so that we know our environment is clean.

Create the CloseLoop directory structure.

Create a dedicated feedback log.

Create a private file containing the pilot data structure but never place real numbers or personal data into the public repository.

The first real goal is not feature development.

The first goal is proving:

```text
CALL-E authentication works
CALL-E runtime tools are available
repository validation works
```

---

# 30. Phase 1 — Write the Safety Contract First

Before writing the engine, define the safety behavior.

The safety document should state what the system may do, what it must refuse to do, and which conditions block a call.

Every future implementation decision should reference this contract.

The safety contract should become executable tests.

The reason for doing this first is architectural. If safety is added after the engine is written, the engine will develop hidden assumptions about when it is allowed to create a side effect.

The source plan explicitly instructs that the safety contract should be written before the engine because it shapes the engine itself.

---

# 31. Phase 2 — Define the Internal Data Model

Create typed models for:

```text
WorkflowSpec
OutcomeContract
ContactRung
Policy
CallAttempt
CallPlan
CallRun
OutcomeResult
Evidence
AuditEntry
ExecutionLedger
WorkflowResult
```

The data models should be provider-independent.

CALL-E-specific fields should live inside the adapter response models rather than inside the generic workflow model wherever possible.

This gives the project a clean boundary:

```text
provider-specific details
            |
            v
       CALL-E adapter
            |
            v
 provider-independent CloseLoop state
```

---

# 32. Phase 3 — Build the FakeAdapter

The FakeAdapter should be complete before the real adapter.

It should simulate:

```text
auth status
tools check
plan
run
status
```

It should load fixtures according to the workflow scenario.

It should generate deterministic run IDs.

It should simulate bounded polling.

It should behave like the real adapter's interface.

At this stage the entire CloseLoop engine should already work without a single real phone call.

---

# 33. Phase 4 — Build the Specification Validator

The validator should reject invalid workflows before they can reach CALL-E.

It should validate:

```text
run_id
outcome name
deadline
quiet hours
timezone
JSON schema
stop condition
strategy
phone numbers
consent basis
maximum attempts
maximum total calls
policy values
writeback target
```

The validator's output should be machine-readable.

For example:

```json
{
  "ok": true,
  "value": {}
}
```

or:

```json
{
  "ok": false,
  "errors": [
    {
      "path": "ladder[1].consent_basis",
      "message": "consent_basis is required"
    }
  ]
}
```

This validator should be a standalone testable component.

---

# 34. Phase 5 — Build the Safety Engine

The safety engine should run immediately before every potential real side effect.

The sequence should be:

```text
load workflow
→ validate workflow
→ check kill switch
→ check suppression list
→ check quiet hours
→ check call budget
→ check idempotency
→ render goal
→ CALL-E plan
→ inspect plan
→ final safety gate
→ CALL-E run
```

The last gate is important.

The system should not assume that because its input was correct, the returned CALL-E plan must also be correct.

The plan should be inspected.

---

# 35. Phase 6 — Build the Orchestration Engine

The engine should implement:

```text
single
cascade
escalation
quorum
verify
human_review
```

but the first fully production-tested strategy should be `cascade`.

Once cascade is correct, the other strategies can reuse the same primitives.

The engine should use a state machine rather than deeply nested conditional logic.

A conceptual state machine is:

```text
INIT
 |
 v
PREFLIGHT
 |
 v
READY
 |
 v
PLANNING
 |
 v
PLAN_APPROVED
 |
 v
RUNNING
 |
 v
RESULT_RECEIVED
 |
 v
RESULT_VALIDATED
 |
 +------ CLOSED
 |
 +------ RETRY
 |
 +------ NEXT_RUNG
 |
 +------ SCHEDULE
 |
 +------ HUMAN_REVIEW
 |
 +------ BLOCKED
 |
 v
TERMINATED
```

This will make testing and future strategy extensions much easier.

---

# 36. Phase 7 — Build Outcome Routing

After each call, the engine must translate the raw result into an outcome class.

The router then looks up the policy for that outcome.

For example:

```text
confirmed
→ close

reschedule
→ create follow-up

no_answer
→ retry if permitted

voicemail
→ consume voicemail allowance and advance

callback_requested
→ schedule retry

wrong_person
→ transfer once or advance

hard_refusal
→ suppress and stop

ambiguous
→ human review

error
→ fail closed
```

The routing table should be data-driven.

That allows new workflows to use different policies without modifying the engine code.

---

# 37. Phase 8 — Build the Ledger

The ledger should be persistent.

SQLite is a good initial choice because it requires minimal operational infrastructure while providing transactional storage.

The ledger should store:

```text
workflow ID
execution key
rung
attempt
status
plan ID
external run ID
started at
completed at
outcome class
result hash
```

The ledger should not store secrets or unnecessary personal data.

The engine should be restartable.

A restart should recover from the ledger rather than recomputing the workflow from scratch.

---

# 38. Phase 9 — Build the Real CLI Adapter

Once the FakeAdapter passes all tests, implement the CLI adapter.

The implementation should invoke:

```text
calle auth status
calle mcp tools
calle call plan
calle call run
calle call status
```

The command output should be parsed as JSON.

The adapter should normalize provider-specific errors into internal CloseLoop errors.

The adapter should never leak credentials.

The adapter should preserve plan IDs and confirmation tokens exactly where required but should not display them in logs.

The source plan explicitly confirms that CLI output can be consumed as JSON by a Python engine and identifies the CLI plus MCP path as a stable runtime integration.

---

# 39. Phase 10 — Optional MCP Adapter

The MCP adapter should expose the same interface as the CLI adapter.

It should target:

```text
plan_call
run_call
get_call_run
```

This gives us a second runtime path without changing the orchestration engine.

The purpose of the MCP adapter is not to duplicate logic.

It is to prove the provider boundary is genuinely abstracted.

---

# 40. Phase 11 — Optional API Adapter

Only build this if the CALL-E account has access to the required API surfaces without introducing deadline risk.

The API adapter should use:

```text
result_schema
metadata
workflow_run_id
Idempotency-Key
```

This is a high-value depth signal because it demonstrates that CloseLoop is leveraging the API's structured-outcome semantics rather than using CALL-E solely as a basic outbound dialing mechanism.

The API should remain a feature-flagged adapter.

The core project must continue to work if the API is unavailable.

---

# 41. Phase 12 — Writeback

The first writeback target should be CSV.

The result should be structured so that an operator can directly see:

```text
record ID
status
outcome
reason
closed rung
calls placed
calls avoided
timestamp
```

Only after this is reliable should a second sink be considered.

A Google Sheets or webhook sink can become a stretch feature.

The source plan explicitly recommends CSV as the first practical writeback path and identifies Sheets/webhooks as additional targets.

---

# 42. Phase 13 — Repository Skill

After the runtime is stable, convert the architecture into the repository-facing Skill.

The Skill should tell an agent host how to invoke CloseLoop.

It should not contain the entire codebase.

The documentation should explain:

```text
what the skill does
when to use it
when not to use it
how to construct a workflow
how safety works
how runtime execution works
what the result looks like
how to dry-run
how to cancel
```

All supporting documentation should live in references where appropriate.

This matches the repository's progressive-disclosure style.

---

# 43. Phase 14 — Repository Validation

Run:

```bash
python3 scripts/check_branch_name.py --branch feat/outcome-close-chain
```

Run:

```bash
python3 scripts/validate_repository.py
```

Run the entire project test suite.

Run all dry-run examples.

Run security checks.

Search the diff for:

```text
phone numbers
tokens
secrets
credentials
private data
```

Confirm that all repository-facing content is in English where required.

Confirm that sample numbers are fictional.

Confirm that the README resource list has the exact required entry.

Confirm that the Skill directory name matches the Skill frontmatter name.

The original submission checklist explicitly requires these repository validations before the PR is opened.

---

# 44. Phase 15 — Live CALL-E Smoke Test

Only after the dry-run engine is stable should the first real call happen.

The first call should use the development team's own consenting test number.

The purpose is only:

```text
authentication
planning
inspection
execution
status
result
```

Do not attempt the full pilot yet.

Save the runtime shape of the result.

If CALL-E behaves differently from documentation, record it in `FEEDBACK.md`.

This is especially valuable because the hackathon's feedback prize rewards concrete, reproducible technical observations.

---

# 45. Phase 16 — Outcome-State Capture

We should deliberately test controlled real-world states.

The aim is to observe how CALL-E represents:

```text
no answer
voicemail
callback request
successful completion
```

The exact state shapes should become fixtures.

Do not invent fixture shapes based only on assumptions.

The fixture files should reflect what CALL-E actually returns.

This makes the dry-run engine much more credible.

---

# 46. Phase 17 — Live Pilot

The pilot should use a very small sample.

The objective is not statistical significance.

The objective is proof that the problem exists and that the system changes the operational workflow.

The pilot report should contain a before table and after table.

The results must be honest.

If the pilot is only three people, say three.

If the system saves ten minutes, say ten.

Do not manufacture a large productivity claim from a tiny sample.

The source plan specifically says an honest small pilot is better than an unverifiable large one.

---

# 47. Phase 18 — Production-Quality Hardening

After the pilot, we should assume something will break.

The hardening pass should focus on:

```text
duplicate execution
timeout behavior
partial results
schema failures
unexpected call states
wrong recipient
late callback
budget exhaustion
quiet hours
restart recovery
suppression
kill switch
masked output
```

Every observed failure should become either:

```text
a regression test
```

or:

```text
a documented known limitation
```

No discovered behavior should disappear into the chat history without entering the engineering record.

---

# 48. Phase 19 — Second-Level Features

Only after the core is reliable should we add additional strategy types.

The likely priority order is:

```text
cascade
→ escalation
→ quorum
→ verification
→ human review
→ scheduling
→ parallel
```

This lets the architecture demonstrate that it generalizes across multiple repository patterns.

A useful demonstration matrix would show:

```text
Placement confirmation     → cascade
Incident acknowledgement   → escalation
Volunteer recruitment      → quorum
Business verification      → verify
Approval capture           → human-review
Callback workflow          → schedule
```

These do not all need real phone calls.

Most can be represented through dry-run fixtures.

---

# 49. Phase 20 — Optional Integrations

If the system is stable, add one integration.

The best first candidate is n8n.

The workflow would be:

```text
n8n trigger
→ CloseLoop workflow
→ CALL-E
→ structured result
→ n8n result handling
```

A second integration should only be attempted if the first one is fully tested.

Do not spend the last days implementing four incomplete integrations.

---

# 50. Phase 21 — The Demo Console

If the runtime is stable, build a minimal interface.

It should not require a complex frontend.

The most useful view is a workflow timeline.

For example:

```text
10:04:12  Workflow created
10:04:13  Safety preflight passed
10:04:13  Candidate plan created
10:04:14  Plan verified
10:04:14  CALL-E call started
10:05:33  Structured result received
10:05:33  Schema validation passed
10:05:33  Stop condition satisfied
10:05:33  Workflow closed
```

Then:

```text
Calls placed: 1
Calls avoided: 2
```

That is enough.

---

# 51. Phase 22 — Demo Scenario Design

The demo should contain one live path and several deterministic dry-run paths.

The live path should demonstrate the central success story.

The first dry-run should demonstrate cascade early exit.

The second dry-run should demonstrate escalation after no-answer and voicemail.

The third should demonstrate hard refusal and suppression.

The fourth can demonstrate a Hindi rung.

The purpose of the dry-runs is to show behavior that is difficult or expensive to reproduce safely with real calls.

---

# 52. The Three-Minute Video

The final video should be approximately two minutes and forty-five seconds.

The first fifteen seconds should establish the problem.

The next twenty seconds should establish the outcome contract and contact strategy.

The real call should appear before forty seconds.

The remainder should show the structured result, policy routing, calls avoided, safety behavior, and reusability.

A recommended narrative is:

```text
Fourteen manual attempts to close four interview slots.

CloseLoop takes an outcome contract instead of a script.

It knows the result it needs, the contacts it is allowed to use, the conditions under which it must stop, and the maximum number of calls it may place.

The candidate answers.

CALL-E returns the structured result.

The schema validates.

The stop rule is satisfied.

CloseLoop stops.

It placed one call instead of continuing through the remaining contacts.

Now we simulate a refusal.

The refusal creates a permanent suppression state.

The chain cannot continue.

The same runtime can express escalation, quorum, and verification workflows.

CloseLoop does not optimize for more calls.

It optimizes for a closed outcome.
```

The source plan recommends putting the live call before the forty-second mark, showing the `plan → run → status` lifecycle, showing structured data flowing into the operational system, showing calls avoided, showing the refusal stop, and ending with the install command.

---

# 53. Video Production Rules

The live call should be recorded in an unbroken take.

The phone number should be masked.

The terminal should be legible.

Subtitles should be included.

No background music should compete with the live call.

If a backup recording is used, it should be clearly represented as a prior successful run rather than deceptively presented as live.

The public video should be available before submission and verified from an incognito window.

The source plan specifically recommends YouTube or Vimeo and public visibility rather than an inaccessible private or unlisted link.

---

# 54. Devpost Submission

The Devpost submission should be treated as the final narrative layer.

The title should be:

```text
CloseLoop
```

The tagline should communicate outcome orchestration.

A strong tagline is:

```text
Outcome-driven phone orchestration for CALL-E — close the goal, not the call.
```

The written description should begin with the thesis:

> Every calling platform optimizes for calls placed. CloseLoop optimizes for outcomes closed.

The problem section should then explain the concrete coordination problem.

The solution section should explain the outcome contract, strategy, CALL-E integration, routing engine, safety layer, idempotency, and writeback.

The impact section should show the actual before-and-after pilot numbers.

The technical section should show the runtime lifecycle.

The community section should explain why the contribution is reusable.

The submission should then include the GitHub PR URL, public video URL, CALL-E account email, and optional demo URL.

The original plan's submission checklist identifies these elements explicitly.

---

# 55. GitHub PR Strategy

The first PR should be the primary contribution.

It should contain the repository-facing Skill, the references, examples, fixtures, documentation, safety material, and validation.

The PR description should contain:

```text
problem
architecture
what is new
how CALL-E is used
safety behavior
dry-run instructions
test results
demo video
```

A companion application should only be a second PR when the primary Skill is already stable.

This preserves contribution scope.

The source plan explicitly warns against creating an unscoped combination of Skill, app, plugin, scheduler recipe, and safety pattern in a single contribution.

---

# 56. README Integration

The repository README resource list should include the Skill using the required repository format.

The entry should explain in one sentence why the Skill is useful for AI-agent phone workflows.

The wording should match the repository conventions rather than reading like marketing copy.

After modifying the README, repository validation must be rerun.

---

# 57. Feedback Prize Strategy

The feedback should be treated as an engineering artifact.

Create:

```text
FEEDBACK.md
```

from the beginning.

Each entry should record:

```text
environment
command
expected behavior
actual behavior
exact error or JSON
impact
proposed improvement
```

The most valuable observations are likely to come from:

```text
CLI behavior
MCP behavior
resultValidation
partial result semantics
idempotency
regional language behavior
documentation gaps
```

The source plan specifically recommends maintaining a running feedback file and collecting concrete reproductions rather than generic comments such as "more documentation would be nice."

---

# 58. Final Validation Matrix

Before submission, the project should pass the following conceptual matrix.

| Area | Required evidence |
|---|---|
| CALL-E runtime | Real `plan → run → status` execution |
| Dry run | Complete fixture-driven execution with zero calls |
| Schema | Valid and invalid result tests |
| Safety | Consent, quiet hours, budget, suppression, kill switch |
| Idempotency | Restart does not duplicate terminal calls |
| Routing | Distinct behavior for major outcome classes |
| Early exit | Calls avoided are measurable |
| Evidence | Structured provenance available where supported |
| CLI | Working primary adapter |
| MCP | Working secondary adapter if available |
| API | Optional preview integration if available |
| Repository | `validate_repository.py` passes |
| Skill | Correct frontmatter and directory structure |
| Documentation | Setup, side effects, cancellation, safety, dry-run |
| Pilot | Real before/after evidence |
| Demo | Real call before forty seconds |
| Submission | GitHub PR + video + description + CALL-E email |
| Feedback | Concrete technical feedback recorded |

---

# 59. Success Criteria for the Project

CloseLoop should not be considered complete because every planned file exists.

It is complete when the following story is demonstrably true.

A user can define a real-world outcome.

The outcome has a machine-readable contract.

The workflow defines who may be called and why.

The system can run without making a real call.

The same workflow can then run through CALL-E.

The CALL-E plan is inspected before execution.

The real call generates a structured result.

The result is validated.

The engine determines whether the outcome is closed.

If the outcome is closed, no additional call is made.

If the outcome is not closed, the system chooses the next action according to policy.

If the person refuses further calls, the entire chain stops.

If the application crashes, it does not silently redial a terminal attempt.

The final result is written into the operational record.

The system reports how many calls it placed and how many calls it avoided.

A stranger can understand all of this from the demo.

The repository contribution can be installed and dry-run without placing real calls.

That is the definition of done.

---

# 60. What We Should Explicitly Not Do

We should not build a generic voice-chat UI.

We should not build a mass-calling campaign tool.

We should not introduce a custom LLM merely to claim that the product uses AI.

We should not build a frontend before the orchestration engine works.

We should not depend exclusively on a preview API.

We should not spend real CALL-E calls debugging orchestration logic.

We should not claim real-world impact without measurements.

We should not submit a huge PR containing unrelated contributions.

We should not expose real pilot phone numbers.

We should not make the system automatically override a refusal.

We should not automatically make high-impact medical, legal, financial, or emergency decisions.

We should not make the product appear autonomous by hiding the fact that safety gates exist.

The strength of CloseLoop is precisely that it is autonomous within a clearly bounded policy.

---

# 61. Recommended Technology Stack

Python should be the primary runtime language.

Python gives us straightforward subprocess integration with the CALL-E CLI, typed models through standard Python tooling, SQLite for the ledger, JSON Schema validation, and strong testing support.

The repository-facing helper scripts should use Node.js and `.mjs` where they need to match the existing Skill conventions.

The core engine should use a modular adapter architecture.

SQLite should be used for the initial execution ledger.

JSON Schema should define structured result contracts.

YAML should define workflow specifications.

CSV should be the first writeback target.

FastAPI can be introduced for an optional web interface.

React can be used for an optional console if the console is justified by demo value.

Redis or PostgreSQL should not be introduced unless a real deployment requirement appears.

The goal is not technological complexity.

The goal is clear separation of concerns and reliable behavior.

The source plan independently arrives at Python as the preferred engine language because the repository already has Python application precedent and the CLI adapter naturally maps to `subprocess` plus JSON parsing.

---

# 62. Final Architecture

The final conceptual architecture should look like this:

```text
                         USER / WORKFLOW
                                |
                                v
                         closeloop.yaml
                                |
                                v
                       Specification Validator
                                |
                                v
                         Safety Preflight
                                |
                   +------------+------------+
                   |                         |
              Call Policy               Evidence Policy
                   |                         |
                   +------------+------------+
                                |
                         Strategy Engine
                                |
        +-----------------------+-----------------------+
        |            |            |          |          |
      SINGLE       CASCADE     ESCALATE    QUORUM    VERIFY
        |            |            |          |          |
        +------------+------------+----------+----------+
                                |
                         Idempotency Ledger
                                |
                         CALL-E Adapter
                                |
              +-----------------+-----------------+
              |                 |                 |
             CLI               MCP               API
              |                 |                 |
              +-----------------+-----------------+
                                |
                              CALL-E
                                |
                              Phone
                                |
                         Structured Result
                                |
                       Schema Validation
                                |
                         Evidence Layer
                                |
                         Outcome Router
                                |
        +-----------------------+-----------------------+
        |                       |                       |
       CLOSE                  CONTINUE              HUMAN REVIEW
        |                       |                       |
        |                Retry / Next Rung /       Structured
        |                  Schedule / Escalate      handoff
        |                       |
        +-----------------------+
                                |
                           Result Envelope
                                |
                    +-----------+-----------+
                    |                       |
                  CSV                   Webhook
                    |
                 Operator
```

This is the architecture we should build toward.

---

# 63. Execution Calendar

The hackathon deadline should not be treated as the moment when coding stops.

The internal schedule should front-load engineering and move submission work earlier.

## September 2

Complete repository and challenge reconnaissance.

Set up CALL-E.

Validate authentication.

Validate the CLI and MCP capabilities.

Create the project repository.

Create the branch.

Create `FEEDBACK.md`.

Define the problem and pilot measurement.

Create the safety contract.

## September 3

Finish the workflow specification.

Define the internal data model.

Define the outcome model.

Define the strategy interface.

Define the shared result envelope.

Implement specification validation.

Do not spend real calls except for essential CALL-E connectivity testing.

## September 4

Implement the FakeAdapter.

Implement the state machine.

Implement the safety engine.

Implement schema validation.

Implement the first cascade strategy.

Get the complete workflow working with fixtures.

## September 5

Implement idempotency.

Implement SQLite ledger.

Implement outcome-class routing.

Implement suppression behavior.

Implement kill-switch behavior.

Expand fixture coverage.

## September 6

Implement the CALL-E CLI adapter.

Perform the first controlled real end-to-end call.

Capture real result shapes.

Update fixtures.

Record all surprises in `FEEDBACK.md`.

## September 7

Implement MCP adapter where feasible.

Add CSV writeback.

Complete the offline tests.

Run repository validation.

Begin writing repository-facing documentation.

## September 8

Run the first real mini-pilot.

Measure the manual process.

Run the equivalent CloseLoop workflow.

Measure the automated process.

Fix reliability issues.

## September 9

Add evidence extraction.

Add the bilingual rung.

Add a second strategy such as escalation or quorum.

Only proceed if the core workflow is stable.

## September 10

Complete `SKILL.md`.

Complete reference documentation.

Complete examples and fixtures.

Complete safety documentation.

Complete README entry.

Run repository validation.

Open the primary GitHub PR as early as possible.

## September 11

Build the minimal results console if needed.

Polish CLI output.

Prepare the demo workflow.

Conduct multiple dry-run rehearsals.

Conduct at least one controlled live rehearsal.

## September 12

Record the final demo.

Create a backup recording.

Create the Devpost draft.

Add the actual pilot measurements.

Verify every claim.

## September 13

Finalize GitHub PR.

Verify public video.

Verify demo URL.

Finalize Devpost.

Submit feedback survey material.

Perform a final security and secrets scan.

## September 14

Submit the Devpost entry well before the official deadline.

Do not make major architectural changes.

Only perform fixes that cannot threaten the working state.

The original source timeline similarly emphasizes opening the PR before the last day, recording the video before submission, and keeping the final day primarily for packaging and submission.

---

# 64. The Submission Checklist

The project should not be submitted until the repository contribution is valid.

The branch name should follow repository conventions.

The Skill directory and frontmatter name should match.

The required README resource entry should exist.

No real numbers or private information should exist in the public diff.

No secrets should exist in the repository.

The dry-run path should work with zero credentials.

The setup instructions should be reproducible.

The side effects should be documented.

Cancellation should be documented.

The repository validator should pass.

The tests should pass.

The GitHub PR should be open and public.

The demo should be public.

The demo should be under three minutes.

The demo should show CALL-E being used at runtime.

The Devpost page should contain the PR URL.

The Devpost page should contain the video URL.

The Devpost page should contain the CALL-E account email.

The Devpost page should contain the project description.

The Devpost description should clearly explain the problem, solution, architecture, CALL-E usage, safety, impact evidence, and reuse.

The feedback survey should be submitted when appropriate.

---

# 65. The Final Pitch

The final pitch should remain extremely simple.

CloseLoop is not:

> an AI that makes calls.

CloseLoop is:

> **an orchestration engine that turns a real-world objective into a verifiable outcome and uses the minimum number of CALL-E calls required to close it.**

The strongest three statements are:

> **Every calling platform optimizes for calls placed. CloseLoop optimizes for outcomes closed, and the calls it does not make are the product.**

> **The stop condition is a JSON Schema, not a vibe. The agent knows it is done because the structured result satisfies the outcome contract.**

> **A refusal stops the entire chain. A phone call is a real-world side effect, so the system treats safety as part of orchestration rather than an afterthought.**

These statements capture the conceptual, technical, and safety differentiation simultaneously.

---

# 66. Final Strategic Decision

The project should now be considered two things simultaneously.

The first is a polished flagship workflow for confirmation-chain coordination.

The second is a reusable orchestration framework capable of representing multiple classes of phone-agent workflows already emerging throughout the CALL-E ecosystem.

We should not attempt to reproduce every Skill and every plugin as independent software.

Instead, we should implement the primitives that make those workflows expressible.

The repository's existing workflows then become evidence that our abstraction is broadly useful.

The flagship demo remains simple.

The underlying architecture remains deep.

The live call proves that CALL-E is genuinely integrated.

The dry-run proves that the contribution is maintainable.

The safety engine proves that we understand real-world side effects.

The idempotency ledger proves production maturity.

The before/after pilot proves real-world impact.

The shared result envelope proves ecosystem awareness.

The GitHub PR proves community contribution.

The Devpost video proves product clarity.

That combination gives us the best chance of maximizing all four judging criteria without turning the hackathon into an uncontrolled attempt to build an entire calling platform from scratch.