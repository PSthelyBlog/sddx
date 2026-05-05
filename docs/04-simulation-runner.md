# Simulation Runner

## Purpose

The simulation runner is the environment in which state machines are operated. It manages instances, routes events, executes transitions, advances virtual time, and exposes the full system state for inspection. It is **not** throwaway tooling — it persists as the project's domain-level test harness indefinitely.

For the YAML scenario language built on top of the runner, see [08-scenario-language](08-scenario-language.md). For the project-level CLI, see [10-cli](10-cli.md).

## Runner Lifecycle

```
Initialize ──▶ Load Machines ──▶ Run Scenarios ──▶ Inspect ──▶ Report
                                       │                         │
                                       ◀─────── (iterate) ───────┘
```

### Initialization

```python
from sddx import SimulationRunner

runner = SimulationRunner()

# Register machine classes
runner.register(OrderLifecycle)
runner.register(PaymentFlow)
runner.register(InventoryState)

# Register instance resolvers
by_order = lambda e: e.payload.get("order_id")
runner.register_resolver(OrderLifecycle, by_order)
runner.register_resolver(PaymentFlow, by_order)
runner.register_resolver(InventoryState, by_order)
```

`register()` wires up both exact and pattern subscriptions declared by the machine class. `register_resolver()` tells the runner how to extract instance IDs from event payloads.

### Machine Instance Management

The runner maintains a registry of live machine instances, keyed by `(MachineClass, instance_id)`:

```python
# Explicit creation
order = runner.create(OrderLifecycle, "ord_123", context={
    "order_id": "ord_123",
    "customer_id": "cust_456",
    "items": [{"sku": "WIDGET", "qty": 2}],
})

# Implicit creation via event routing.
# If an event arrives for an instance that doesn't exist,
# the runner creates it with context seeded from the event payload.
```

Each instance receives the runner's `EventBus` and `VirtualClock` automatically.

Machines are never accessed directly during simulation. All interaction goes through the runner, which ensures events are properly routed and logged.

## Executing Transitions

### Direct Firing

```python
result = runner.fire("ord_123", OrderLifecycle, "validate")
```

This is the primary mechanism during scenario execution.

### Event-Driven Firing

When a machine emits an event, the runner handles delivery automatically:

1. Match the event name to subscriptions across all registered machine classes (exact + pattern).
2. Resolve the target instance for each subscribing machine class.
3. Fire the declared transition on the target instance.
4. Collect results and any cascading events.
5. Repeat until the event queue is empty.

The runner returns a `StepResult` capturing everything that happened:

```python
@dataclass
class StepResult:
    trigger: str                           # What initiated this step
    transitions_fired: list[TransitionRecord]
    events_emitted: list[Event]
    cascade_depth: int                     # How deep the event chain went
    errors: list[StepError]                # Any failures during the step
```

## Virtual Time

The runner owns a `VirtualClock`. Machines schedule delayed events via `self.set_timer(name, delay, payload)`:

```python
class PaymentFlow(StateMachine):
    pending = State(initial=True)
    authorized = State()
    expired = State(final=True)

    authorize = pending.to(authorized)
    expire = pending.to(expired)

    def on_transition_authorize(self) -> None:
        self.set_timer("payment.timeout", 24 * 3600, {
            "order_id": self._instance_id,
        })

    @classmethod
    def subscriptions(cls):
        return {"payment.timeout": "expire"}
```

To drive simulation time forward:

```python
fired = runner.advance_time(seconds=86400)  # 24 hours
# Returns the list of events emitted as scheduled timers fired.
```

Cascades from those events propagate the same way as any other event delivery. Wall-clock time is never used during simulation.

The clock is exposed read-only via `runner.clock`. Negative delays or negative advances raise `ValueError`.

## Inspection

### System-Level

```python
runner.machine_states()
# Returns: {
#   (OrderLifecycle, "ord_123"): "completed",
#   (PaymentFlow, "ord_123"): "captured",
#   (InventoryState, "ord_123"): "allocated",
# }

runner.active_instances()  # non-final instances
runner.final_instances()   # final-state instances
runner.event_log           # EventLog with query support
runner.clock.now()         # current virtual time
```

### Instance-Level

```python
order = runner.get(OrderLifecycle, "ord_123")
order.current_state          # "completed"
order.history                # [TransitionRecord, ...]
order.context                # {"order_id": "ord_123", ...}
order.available_transitions  # [] (final state)
```

### Structural

These operate on machine **classes**, not instances, and are used during the structural pass before scenarios run:

```python
runner.reachability(OrderLifecycle)         # set of reachable state names
runner.dead_states(OrderLifecycle)          # unreachable states
runner.termination_issues(OrderLifecycle)   # non-final states with no path to a final state
runner.dead_letters()                       # emitted events with no subscriber (excludes telemetry)
runner.phantom_subscriptions()              # subscribed events never emitted
runner.check()                              # full StructuralReport
```

The `StructuralReport.is_valid` property is `True` iff every machine has clean reachability and termination AND there are no dead letters or phantom subscriptions.

## Reset and Isolation

Each scenario runs in a clean environment:

```python
runner.reset()
# Destroys all machine instances, clears the event log, resets the clock.
# Retains machine class registrations and resolvers.
```

Scenarios do not share state. This guarantees that scenario results are independent and reproducible.

## Snapshot / Restore

The runner can capture and reconstruct full simulation state — every instance, the event log, and the virtual clock:

```python
snap: dict = runner.snapshot()
# JSON-serializable. Persist anywhere.

# Later — possibly in a different process:
fresh = SimulationRunner()
fresh.register(OrderLifecycle)
fresh.register(PaymentFlow)
fresh.register(InventoryState)
fresh.register_resolver(OrderLifecycle, by_order)
# ... same registrations as before ...

fresh.restore(snap)
```

Required: the destination runner must already have the same machine classes registered. Subscriptions and resolvers are properties of the runtime, not the state, so they are not part of the snapshot.

This enables long-running sessions to pause across process restarts, durable agents that survive crashes, and time-travel debugging during development.

## Determinism

The runner is fully deterministic. Given the same registrations, the same sequence of stimuli (including `advance_time` calls), and the same guard logic, the output is identical across runs. There is no concurrency, no randomness, and no external dependency.

This is critical for the iterate-and-fix loop. When you modify a machine and rerun a scenario, any difference in results is attributable to the code change, not environmental factors.

## Error Handling

The runner does not crash on transition failures. It records them and continues:

```python
@dataclass
class StepError:
    step_index: int
    machine: str
    instance: str
    transition: str
    error_type: str    # "unknown_transition" | "invalid_source_state" | "no_guard_passed"
    message: str
    machine_state_at_error: str
```

Errors are examined after a scenario run to determine whether they represent bugs (a transition should have been possible but wasn't) or expected behavior (testing a failure path that the scenario marks with `expect_failure`).
