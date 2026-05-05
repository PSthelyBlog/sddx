# Convergence Criteria

## Purpose

Convergence is the point at which the state machine core is considered correct and ready for I/O adaptation. Convergence is not a single check — it is three layers evaluated in order. Each layer must pass before the next is meaningful.

```
Layer 1: Structural Completeness ──▶ Layer 2: Scenario Coverage ──▶ Layer 3: Property Invariants
         (static analysis)                  (simulation)                  (cross-machine logic)
```

Run all three with `python -m sddx -C my-project converge`.

## Layer 1: Structural Completeness

Structural checks run against machine class definitions without creating instances or firing transitions. They verify that the machines are well-formed in isolation and correctly wired together.

### Per-Machine Checks

**Reachability.** Every declared state must be reachable from the initial state through some sequence of transitions. An unreachable state is dead code — it was declared but can never be entered. This is always a modeling error.

**Termination.** Every non-final state must have at least one path (possibly through other states) to a final state. A state with no path to any final state is a potential deadlock — a machine instance could become permanently stuck there.

**Guard completeness.** For every guarded transition (multiple targets), the guards must be exhaustive. Either every branch has an explicit guard, or the last branch has no guard (acting as a default fallback). A guarded transition where all branches have explicit guards and none is guaranteed to pass can result in a `no_guard_passed` failure at runtime.

**Transition naming.** Each transition name within a machine must be unique. Each state name within a machine must be unique.

### Cross-Machine Checks

**No dead letters.** Every event emitted by any machine (found by inspecting `on_enter_*`, `on_exit_*`, and `on_transition_*` methods for `self.emit()` calls) must have at least one subscriber across all registered machines. Subscribers may be exact (`subscriptions()`) or pattern-based (`pattern_subscriptions()`). Events declared as `telemetry_events()` are exempt from the dead-letter check; see [03-event-system](03-event-system.md#telemetry-events).

**No phantom subscriptions.** Every event listed in any machine's `subscriptions()` must be emitted by at least one other machine. Pattern subscriptions are flagged as phantoms only when no emitted event matches the pattern. A subscription to a non-existent event means a machine is waiting for something that will never happen.

**Schema agreement.** Payload field naming should be consistent across the emitter and all subscribers. The framework does not yet validate this automatically (planned for v0.2); for now, scenario failures and invariant violations surface schema mismatches.

### Resolution

Structural failures are reported with specific diagnostics:

```
Layer 1 failed:
  unreachable: {"PaymentFlow": ["refunded"]}
  dead letters: ["order.expired"]
  phantom subs: ["payment.contested"]
```

Fix structural issues before scenarios run. The CLI exits non-zero if any structural check fails.

## Layer 2: Scenario Coverage

Scenario checks verify that the system **behaves correctly** under defined usage patterns. They require running the simulation.

### Scenario Sources

**User-derived scenarios.** Translate the user's original task description into concrete scenarios. "Build an order processing system" yields scenarios like:
- Happy path: order created, validated, paid, fulfilled, completed.
- Payment failure: order created, validated, payment fails, order cancelled.
- Cancellation: order created, validated, user cancels before payment.

**Failure scenarios.** For every guarded transition with a failure branch, generate a scenario that forces the failure path. These are systematic, not creative — one scenario per failure branch.

**Timeout scenarios.** For every time-based transition, generate a scenario where the timeout fires and verifies the system handles it correctly. Use `advance_time:` to drive the virtual clock.

### Coverage Requirements

**Transition coverage.** Every transition in every machine must be exercised by at least one scenario. This is the minimum bar. If a transition has never fired during simulation, there is no evidence it works.

**State coverage.** Every state in every machine must be entered by at least one scenario. This is usually implied by transition coverage but is checked independently.

**Branch coverage.** Every branch of every guarded transition must be exercised. If `validate` can go to either `validated` or `cancelled`, both paths must be taken across the scenario set.

**Event path coverage.** Every event subscription must be triggered at least once.

### Scenario Assertions

Each scenario defines expected outcomes checked after execution:

```yaml
scenario: happy_path_order
narrative: "happy path order to completion"
setup:
  - create: OrderLifecycle("ord_1")
    context:
      order_id: "ord_1"
      customer_id: "c1"
      items: [{ sku: "A" }]
steps:
  - fire: OrderLifecycle("ord_1").validate
  - fire: PaymentFlow("ord_1").authorize
    args: { amount: 29.99 }
  - fire: PaymentFlow("ord_1").capture
  - fire: OrderLifecycle("ord_1").fulfill
  - fire: OrderLifecycle("ord_1").complete
expect:
  states:
    OrderLifecycle("ord_1"): completed
    PaymentFlow("ord_1"): captured
    InventoryState("ord_1"): allocated
  events_emitted:
    - order.validated
    - payment.authorized
    - payment.captured
    - order.fulfilled
    - order.completed
  events_not_emitted:
    - order.cancelled
    - payment.failed
```

See [08-scenario-language](08-scenario-language.md) for the full grammar including `advance_time` and context assertions.

### Resolution

Scenario failures indicate one of three things:

1. **Machine logic is wrong.** A guard rejects valid input, a transition is missing, or a side effect emits the wrong event. Diagnose from the `StepResult` and direct a fix.
2. **The scenario is unrealistic.** The expected outcome doesn't match how the machines should actually behave. Adjust the scenario.
3. **A missing machine or transition.** The scenario requires behavior that hasn't been modeled yet.

Distinguish by examining the failure point: did a transition fail? Did a machine reach an unexpected state? Did an event not propagate?

## Layer 3: Property Invariants

Property invariants are rules that must hold **across** machines. No single machine can enforce them alone — they are emergent properties of the system's event-driven interactions.

### Invariant Definition

Invariants are expressed as assertions over the event log and machine states after any scenario execution. Place them in `invariants/*.py`; functions named `invariant_*` are auto-discovered:

```python
def invariant_payment_before_completion(log, states):
    """An order cannot complete unless its payment was captured."""
    for (machine, instance), state in states.items():
        if machine == "OrderLifecycle" and state == "completed":
            payment_captured = log.filter(
                name="payment.captured",
                source_instance=instance,
            )
            assert len(payment_captured) > 0, (
                f"Order {instance} completed without payment capture"
            )


def invariant_no_double_capture(log, states):
    """Payment capture must occur at most once per order."""
    for instance in log.unique_instances("PaymentFlow"):
        captures = log.filter(name="payment.captured", source_instance=instance)
        assert len(captures) <= 1, (
            f"Payment {instance} captured {len(captures)} times"
        )


def invariant_cancellation_releases_inventory(log, states):
    """A cancelled order whose inventory was reserved must release it."""
    for instance in log.unique_instances("OrderLifecycle"):
        was_cancelled = log.filter(name="order.cancelled", source_instance=instance)
        was_reserved = log.filter(name="inventory.reserved", source_instance=instance)
        if len(was_cancelled) and len(was_reserved):
            was_released = log.filter(name="inventory.released", source_instance=instance)
            assert len(was_released) > 0, (
                f"Order {instance} cancelled after reservation but inventory not released"
            )
```

### Invariant Sources

- **Domain understanding.** Propose invariants based on the task. "Build an order processing system" implies "you can't complete an order without payment" even if never stated explicitly.
- **User as acceptance criteria.** "An order must never be fulfilled without sufficient inventory" is an invariant the user can declare directly.
- **Emerged from simulation.** When you observe surprising behavior during scenario runs, codify the expected behavior as a new invariant. Often these are the most valuable invariants — they capture lessons learned during the iterate-and-fix loop.

### Invariant Evaluation

Every invariant is checked after every scenario execution. An invariant violation means the system permits a sequence of transitions that violates a domain rule.

Invariant violations are the **highest-priority fix** because they indicate that individually correct machines produce collectively incorrect behavior. The fix is usually a missing guard, a missing event subscription, or a missing transition.

### Resolution

When an invariant fails:

1. Identify the scenario that triggered the violation.
2. Examine the event log to find the specific moment the invariant was breached.
3. Determine which machine should have prevented the invalid state — usually by adding a guard that checks for a prerequisite event.
4. Add the guard, rerun all scenarios, and recheck all invariants.

## Convergence Report

When all three layers pass, the CLI prints a convergence summary:

```
Layer 1: structural OK
[PASS] happy_path_order (invariants: 5/5)
[PASS] payment_failure (invariants: 5/5)
[PASS] cancellation_releases_inventory (invariants: 5/5)
[PASS] payment_timeout_expires_order (invariants: 5/5)

4/4 scenarios converged
```

This report is presented to the user (or to a code reviewer, or to CI) as evidence that the domain logic is correct before any infrastructure code is written.
