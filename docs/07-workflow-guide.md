# Workflow Guide

## Overview

This document describes the step-by-step process from receiving a task through delivering a converged, production-ready system. Each phase has clear entry criteria, activities, and exit criteria. The workflow is the same whether a human or an LLM agent (with subagents) is driving.

## Phase 0: Task Reception

**Trigger:** A development task is received.

**First action:** Understand the task well enough to identify domains, entities, and processes. No code yet. Produce a **task decomposition** in plain language.

Example task: *"Build a subscription billing system that handles plan changes, payment retries, and cancellations."*

Decomposition:

```
Domains identified:
  1. SubscriptionLifecycle — manages the subscription from creation to cancellation
  2. BillingCycle — manages recurring charge attempts per billing period
  3. PaymentAttempt — manages individual payment attempts with retry logic
  4. PlanManagement — manages plan changes (upgrades, downgrades)

Key interactions:
  - BillingCycle triggers PaymentAttempt at the start of each period
  - PaymentAttempt failure triggers retry logic within BillingCycle
  - Repeated failures cause BillingCycle to escalate to SubscriptionLifecycle
  - PlanManagement changes affect the next BillingCycle's amount
  - Cancellation in SubscriptionLifecycle halts future BillingCycles

Implied failure paths:
  - Payment fails → retry → retry → escalate → suspend subscription
  - Plan change during active billing period → prorate or defer
  - Cancellation with remaining balance → handle final charge
```

**Exit criteria:** All machines, their primary states, their interactions, and the key failure paths are identified. Reviewable before any implementation begins.

## Phase 1: Subagent Dispatch — Machine Implementation

**Entry criteria:** Task decomposition approved.

**Action:** Dispatch one subagent per machine with a specific brief.

### Subagent Brief Structure

Each subagent receives:

1. **Machine name and purpose** — one sentence describing what this machine tracks.
2. **States** — the expected states, with initial and final markers.
3. **Transitions** — the expected transitions, including failure branches.
4. **Events to emit** — what events this machine should produce, with payload schemas.
5. **Events to consume** — what events from other machines this one reacts to.
6. **Context fields** — what data the machine carries.
7. **Guard hints** — business rules that should gate transitions.
8. **Stdlib hints** — if a `Barrier` or `Retry` from `sddx.std` would do the job, name it.

Example brief for `PaymentAttempt`:

```
Machine: PaymentAttempt
Purpose: Manages a single attempt to charge a customer.

States:
  - pending (initial)
  - processing
  - succeeded (final)
  - failed (final)

Transitions:
  - process: pending → processing
  - succeed: processing → succeeded
  - fail: processing → failed

Events to emit:
  - payment.succeeded {subscription_id, amount, transaction_id}
  - payment.failed {subscription_id, amount, failure_reason, attempt_number}

Events to consume:
  - billing.charge_initiated → triggers "process" transition

Context fields:
  - subscription_id, amount, attempt_number, failure_reason, transaction_id

Guards:
  - process guard: amount must be positive

Stdlib hints:
  - For retry-with-backoff at the BillingCycle level, use sddx.std.Retry.
```

### Subagent Deliverables

Each subagent returns a Python module containing:

- The machine class following the protocol specification.
- Docstrings on every guard explaining the business rule.
- A `subscriptions()` (and optionally `pattern_subscriptions()`) classmethod.

Subagents work independently. They do not see each other's code. Integration is the orchestrator's responsibility.

**Exit criteria:** All machine modules received, each individually passing structural checks (correct protocol usage, exactly one initial state, at least one final state, etc.).

## Phase 2: Assembly

**Entry criteria:** All machine modules received and individually valid.

Assemble the machines into the simulation runner:

1. Register all machine classes (`runner.register(Cls)`).
2. Wire up resolvers (`runner.register_resolver(Cls, lambda e: e.payload[...])`).
3. Run cross-machine structural checks: `python -m sddx -C my-project check`.

This is where integration issues surface. Common problems at this stage:

- **Schema mismatches.** Machine A emits an event with field `order_id` but Machine B's subscription handler expects `subscription_id`. Identify the mismatch and send a targeted fix request to the responsible subagent.
- **Dead letters.** Machine A emits an event nobody listens to. Determine whether a subscription is missing or the event shouldn't be emitted (or it's truly observation-only and belongs in `telemetry_events()`).
- **Phantom subscriptions.** Machine B subscribes to an event nobody emits. Determine whether an emission is missing or the subscription is wrong.

**Exit criteria:** All structural checks pass (Layer 1 convergence).

## Phase 3: Scenario Development

**Entry criteria:** Layer 1 convergence achieved.

Develop scenarios from three sources:

### Happy Path Scenarios

Derived from the task description. These represent the intended normal operation. For the subscription billing example:

- New subscription → first billing → payment succeeds → subscription active
- Plan upgrade → next billing at new rate → payment succeeds

### Failure Path Scenarios

One scenario per failure branch per guarded transition, generated systematically:

- Payment fails on first attempt
- Payment fails on all retry attempts → subscription suspended
- Plan change rejected (e.g., downgrade during grace period)
- Cancellation during pending payment

### Timeout Scenarios

One scenario per time-based transition, using `advance_time:`:

- Payment retry after 24-hour delay
- Subscription suspension after 3 failed billing cycles
- Grace period expiry after cancellation

Each scenario is a YAML file with steps and expected outcomes — see [08-scenario-language](08-scenario-language.md).

**Exit criteria:** Scenario set covers all transitions, all states, all guard branches, and all event subscriptions.

## Phase 4: Simulation Loop

**Entry criteria:** Scenario set developed.

This is the core iterative phase. Run scenarios via `python -m sddx -C my-project run` and act on the results.

```
Run scenarios ──▶ Examine results ──▶ All pass? ──Yes──▶ Phase 5
                        │
                        No
                        │
                        ▼
                  Diagnose failure
                        │
                        ▼
                  Fix or delegate
                        │
                        ▼
                  Rerun scenarios (regression)
```

### Diagnosis Process

When a scenario fails:

1. **Read the StepResult.** Which step failed? What was the error type?
2. **Transition failure (`no_guard_passed`):** The machine rejected the input. Is the guard too strict (bug) or is the scenario feeding invalid data (bad scenario)? Examine the guard logic and the step's arguments to decide.
3. **Unexpected state:** A machine reached a state the scenario didn't expect. Trace the event log to find which event cascade led there. Was it a missing guard? An incorrect subscription? An event emitted with wrong data?
4. **Missing event:** An expected event was never emitted. Check whether the transition that should have emitted it actually fired. If it fired but didn't emit, the `on_enter_*` / `on_transition_*` side effect is wrong. If it didn't fire, trace back to find why.
5. **Cascade issue:** Events propagated in an unexpected order or too deeply. Examine the event log for circular patterns or unexpected subscription triggers.

### Fix Process

Either fix the issue directly (for simple corrections like a typo in a guard condition) or send a targeted fix request to the responsible subagent. Fix requests include:

- The specific machine and location of the problem.
- The scenario that triggered it.
- The expected behavior.
- The actual behavior.
- The event log excerpt showing the failure.

After the fix, rerun the failing scenario AND all previously passing scenarios (regression check).

**Exit criteria:** All scenarios pass. Transition, state, branch, and event path coverage are 100%.

## Phase 5: Invariant Verification

**Entry criteria:** Layer 2 convergence achieved.

Define and check property invariants.

### Invariant Proposal

Propose invariants based on domain understanding:

- Ordering constraints: "X must happen before Y."
- Exclusivity constraints: "X and Y cannot both be true."
- Conservation constraints: "every A must be matched by a B."
- Idempotency constraints: "X can happen at most once."

Place them in `invariants/*.py` as functions named `invariant_*`. Auto-discovered by the CLI.

### Invariant-Driven Discovery

Invariant failures often reveal edge cases that scenarios missed. When an invariant fails:

1. Create a new scenario isolating the violating sequence.
2. Fix the underlying machine logic.
3. Add the new scenario to the permanent scenario set.

This is why invariant checking comes last — it serves as a safety net catching cross-machine issues that per-machine scenario testing might miss.

**Exit criteria:** `python -m sddx -C my-project converge` exits 0. All invariants pass across all scenarios.

## Phase 6: I/O Adaptation

**Entry criteria:** Full convergence (all three layers).

Inventory the I/O requirements:

```
Inbound:
  - HTTP API for subscription creation, plan changes, cancellation
  - Webhook receiver for payment processor callbacks
  - Cron trigger for billing cycle initiation

Outbound:
  - Database persistence (likely runner.snapshot() for whole-session durability)
  - Payment processor API calls
  - Email notifications for billing events
  - Webhook emissions for partner integrations
```

Dispatch subagents to write adapters. Each adapter brief includes:

- Which events or transitions it handles.
- The external system's interface (API schema, database schema).
- Error handling requirements (retry policy, circuit breaker needs).
- The adapter design rules from the I/O Adapter specification.

Adapters are tested independently against mocked infrastructure.

**Exit criteria:** All adapters written, individually tested, and attached to the production runner. Integration test with containerized infrastructure passes the same scenarios as simulation.

## Phase 7: Delivery

**Entry criteria:** Integration tests pass.

Deliver to the user:

1. **Convergence report** — proof that the domain logic is correct.
2. **Machine source code** — the production core.
3. **Adapter source code** — the infrastructure layer.
4. **Scenario set** — reusable as regression tests.
5. **Invariant set** — reusable as property-based checks.
6. **Project layout** — `machines/`, `scenarios/`, `invariants/`, `sddx_project.py` — for ongoing development and debugging.

The simulation infrastructure remains active. When the user requests changes, the process begins again at Phase 0, with existing machines and scenarios as the starting point.
