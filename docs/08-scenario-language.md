# Scenario Language

## Purpose

Scenarios are the executable specs that drive Layer 2 convergence. They are written in YAML so they read as documentation, render in code review, and can be authored by humans and LLMs alike.

A scenario is a sequence of stimuli (creating instances, firing transitions, advancing time) followed by assertions about the resulting state.

## File Structure

```yaml
scenario: <unique_name>          # required, snake_case
narrative: "<one-line summary>"   # optional but recommended

setup:                            # optional; runs before steps
  - <step>
  - <step>

steps:                            # the actual scenario
  - <step>
  - <step>

expect:                           # final assertions after all steps
  states:        { ... }
  events_emitted: [ ... ]
  events_not_emitted: [ ... ]
  context:       { ... }
```

The CLI (`python -m sddx run`) auto-discovers `**/*.yaml` under `scenarios/`. File path is informational; the `scenario:` field is the canonical name.

## Step Types

There are four step types: `create`, `fire`, `advance_time`, and `assert`.

### `create` — instantiate a machine

```yaml
- create: OrderLifecycle("ord_1")
  context:
    order_id: "ord_1"
    customer_id: "cust_1"
    items:
      - sku: "WIDGET"
        quantity: 2
```

The reference syntax is `MachineName("instance_id")`. The instance is created on the runner with the given context (or `{}` if omitted) and remains available for subsequent steps.

Implicit creation also works: events delivered to a non-existent instance create one automatically, seeded with the event payload as context. Use explicit `create:` when you need control over initial context.

### `fire` — fire a transition

```yaml
- fire: OrderLifecycle("ord_1").validate

- fire: PaymentFlow("ord_1").authorize
  args:
    amount: 29.99
    currency: "USD"

- fire: OrderLifecycle("ord_1").complete
  expect_failure: invalid_source_state   # we expect this to fail
```

The fire reference syntax is `MachineName("instance_id").transition_name`. Optional `args:` are passed as kwargs to `fire()` and merged into the machine's context.

`expect_failure` accepts one of:
- `unknown_transition`
- `invalid_source_state`
- `no_guard_passed`

A step with `expect_failure` passes only if the transition fails with the matching reason. Without it, any failure ends the scenario.

### `advance_time` — drive the virtual clock

```yaml
# Number = seconds (most common form):
- advance_time: 30

# Object form supports seconds/minutes/hours/days; values combine:
- advance_time:
    minutes: 5
- advance_time:
    hours: 1
    minutes: 30
- advance_time:
    days: 1
```

The runner's `VirtualClock` advances by the specified amount. Any timers scheduled via `self.set_timer(...)` whose deadline falls within the advance fire as events on the bus, in deadline order.

### `assert` — mid-scenario assertion

```yaml
- assert:
    states:
      Calculator("s1"): operator_pending
      MemoryRegister("s1"): holding
    events_emitted:
      - calc.operator_selected
    events_not_emitted:
      - calc.errored
    context:
      Counter("c1"):
        count: 3
```

All sub-keys are optional. Each is checked independently:

- **`states`** — `{MachineName("id"): expected_state_name}`. The instance must exist and be in the named state.
- **`events_emitted`** — list of event names that must have appeared in the log (in any order).
- **`events_not_emitted`** — list of event names that must NOT appear in the log.
- **`context`** — `{MachineName("id"): {field: expected_value}}`. Direct comparison against `instance.context[field]`.

A failed assertion ends the scenario with a descriptive failure reason.

## The `expect:` Block

After all steps run, the `expect:` block is evaluated once. Same shape as an `assert:` step but tied to scenario completion:

```yaml
expect:
  states:
    OrderLifecycle("ord_1"): completed
    PaymentFlow("ord_1"): captured
  events_emitted:
    - order.completed
    - payment.captured
  events_not_emitted:
    - order.cancelled
  context:
    OrderLifecycle("ord_1"):
      total: 29.99
```

A scenario passes only if every step succeeds AND the `expect:` block matches.

## Worked example

A scenario that tests retry-with-timeout using `advance_time` and a context assertion:

```yaml
scenario: payment_retry_succeeds_on_third_attempt
narrative: "First two payment attempts time out; third succeeds before final cutoff"

setup:
  - create: PaymentFlow("p1")
    context:
      subscription_id: "sub_1"
      amount: 49.99
      attempts: 0

steps:
  - fire: PaymentFlow("p1").attempt
  - advance_time: 30        # first attempt times out
  - assert:
      states:
        PaymentFlow("p1"): backing_off
      context:
        PaymentFlow("p1"):
          attempts: 1

  - advance_time: 60        # backoff expires, attempt 2
  - advance_time: 30        # second attempt times out
  - advance_time: 120       # backoff expires, attempt 3
  - fire: PaymentFlow("p1").succeed

expect:
  states:
    PaymentFlow("p1"): captured
  context:
    PaymentFlow("p1"):
      attempts: 3
  events_emitted:
    - payment.captured
  events_not_emitted:
    - payment.exhausted
```

## Scenario Failures

When a step fails, the scenario halts and reports:

- The failing step index.
- A human-readable reason: `Transition 'X' failed: no_guard_passed - ...` or `State assertion failed for ...` or `Event '...' was not emitted`.

Coverage analysis uses the union of transitions/states/events exercised across all passing scenarios. A scenario that fails contributes nothing to coverage.

## Invariant Integration

The runner accepts a list of invariant functions to evaluate after each scenario. The CLI's `converge` command auto-discovers `invariants/*.py` and applies all `invariant_*` functions to every scenario.

A scenario passes only when:

1. Every step succeeded.
2. The `expect:` block matched.
3. Every invariant passed.

See [05-convergence-criteria](05-convergence-criteria.md#layer-3-property-invariants) for the invariant API.

## Style guidance

- **One concept per scenario.** Don't pile orthogonal failures into one file. A scenario named `divide_by_zero_compute` should test exactly that.
- **Minimum context in setup.** Initial context should be the smallest valid input that exercises the path. Anything else is noise.
- **Assert mid-scenario when ordering matters.** If you care that state X is reached *before* event Y, use an `assert:` step at the right point — don't rely on the final `expect:` block alone.
- **Name the failure mode in the scenario name.** `payment_retry_exhausts_after_three_attempts` is more reviewable than `payment_test_5`.
- **Failure scenarios use `expect_failure`.** Don't write a scenario that "tests" a guard rejecting input by checking final state — assert the failure reason directly.
