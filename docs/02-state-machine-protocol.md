# State Machine Protocol

## Purpose

Every state machine in the system must conform to this protocol. The protocol exists so that the runner can uniformly operate any machine — inspect its state, enumerate available transitions, fire transitions, and observe results — without knowing the machine's domain-specific details.

## Base Class: `StateMachine`

All machines inherit from `sddx.StateMachine`. The base class provides the protocol; subclasses define states, transitions, guards, and side effects.

```python
from sddx import State, StateMachine

class OrderLifecycle(StateMachine):
    """Tracks an order from creation through fulfillment."""

    # --- States ---
    created = State(initial=True)
    validated = State()
    fulfilled = State()
    completed = State(final=True)
    cancelled = State(final=True)

    # --- Transitions ---
    validate = created.to(validated) | created.to(cancelled)
    fulfill = validated.to(fulfilled)
    complete = fulfilled.to(completed)
    cancel = (created | validated | fulfilled).to(cancelled)

    # --- Guards ---
    def guard_validate_to_validated(self, **kwargs) -> bool:
        return bool(
            self._context.get("items") and self._context.get("customer_id")
        )

    # --- Side Effects ---
    def on_enter_validated(self, source: str) -> None:
        self.emit("order.validated", {
            "order_id": self._context["order_id"],
            "items": self._context["items"],
        })

    def on_enter_cancelled(self) -> None:
        self.emit("order.cancelled", {
            "order_id": self._context["order_id"],
            "reason": self._context.get("cancellation_reason", "unspecified"),
        })
```

## Protocol Requirements

### States

Each state is declared as a class-level `State()` descriptor. States have two optional flags:

- `initial=True` — exactly one state per machine must be initial. The machine starts here.
- `final=True` — the machine considers itself complete in this state. No transitions fire from final states.

Every state must be reachable from the initial state. Unreachable states are a structural error caught during convergence.

**Note:** the initial state's `on_enter_*` hook does **not** fire on construction — the machine simply starts in that state. Hooks fire only on actual transitions.

### Transitions

Transitions are declared as class-level descriptors connecting source states to target states.

A transition with a single target is **deterministic**:

```python
validate = created.to(validated)
```

A transition with multiple targets is **guarded** — the machine evaluates guards in declaration order and takes the first branch whose guard returns `True`:

```python
validate = created.to(validated) | created.to(cancelled)
```

A transition can have multiple source states:

```python
cancel = (created | validated).to(cancelled)
```

#### `.loop()` for self-loops

For self-loops, use `.loop()`:

```python
record_input = recording.loop()                   # recording → recording
notify = (idle | active | done).loop()            # all three self-loop
```

This is equivalent to `recording.to(recording)` and `idle.to(idle) | active.to(active) | done.to(done)` respectively, but reads cleaner.

### Guards

Guards return `bool`. They receive the same keyword arguments passed to the transition call and read `self._context`.

Two naming conventions are honored, in order:

1. **Per-source** (preferred when the same target needs different rules from different sources):

   ```python
   guard_{transition}_from_{source}_to_{target}
   ```

2. **Target-only** (fallback):

   ```python
   guard_{transition}_to_{target}
   ```

The framework tries the per-source form first, then the target-only form. Either may be present; both is fine but per-source wins. Example:

```python
class Calculator(StateMachine):
    select_operator = (
        entering_first.to(operator_pending)
        | entering_second.to(operator_pending)
        | entering_second.to(error)
    )

    def guard_select_operator_from_entering_first_to_operator_pending(self, **kw):
        return True  # always allowed from entering_first

    def guard_select_operator_from_entering_second_to_operator_pending(self, **kw):
        return not self._would_divide_by_zero()

    def guard_select_operator_from_entering_second_to_error(self, **kw):
        return self._would_divide_by_zero()
```

If no explicit guard is defined for a branch, it defaults to `True` (always allowed). For guarded transitions (multiple targets), the last branch typically has no guard and serves as the fallback.

Guards must be **pure functions of machine context and transition arguments**. They must not perform I/O, access external state, or mutate context.

### Context

Every machine instance carries a `context: dict` — a mutable dictionary holding the machine's working data. Context is initialized at machine creation and updated through transition arguments.

```python
order = OrderLifecycle(context={
    "order_id": "ord_123",
    "customer_id": "cust_456",
    "items": [{"sku": "A1", "qty": 2}],
})
```

When a transition fires, keyword arguments are merged into context:

```python
order.fire("cancel", cancellation_reason="customer_request")
# order.context["cancellation_reason"] is now "customer_request"
```

Context is the mechanism by which machines accumulate information over their lifecycle.

**Naming caveat:** if you subscribe to an event whose payload key collides with a context field you care about, the kwarg merge will overwrite it silently. Choose disjoint key names — e.g. MemoryRegister stores its state under `memory` and reads incoming requests as `value`.

### Side Effects

Side effects are methods triggered by state changes:

- `on_exit_{state}()` — called when leaving a state.
- `on_enter_{state}()` — called when entering a state.
- `on_transition_{transition}()` — called when a transition fires, after `on_exit` and before `on_enter`.

Side effects may read and write context. Their primary purpose is emitting events via `self.emit(event_name, payload)`. They should **not** perform I/O directly.

#### Source / target injection

Hooks may declare optional `source: str` and/or `target: str` parameters. The framework inspects each hook's signature and supplies whichever it accepts:

```python
def on_transition_compute(self, target: str) -> None:
    if target == "error":
        self._context["error_reason"] = "Division by zero"
        return
    # ... happy-path math ...

def on_enter_idle(self, source: str) -> None:
    if source != "":
        self.emit("calc.cleared", {...})
```

Vanilla hooks with no parameters keep working — the framework only injects what the signature accepts. This eliminates two common footguns:

1. `self._history[-1]` does NOT contain the current transition during `on_enter_*`. The history is appended *after* `on_enter` returns. Use the `source` parameter instead.
2. Self-loops still fire `on_enter_*` (the state machine "exits and re-enters" the same state). If your hook should distinguish a fresh entry from a self-loop, branch on `source`.

### Event Emission

Machines communicate by emitting events: `self.emit("order.validated", payload)`. Emitted events are delivered to all subscribers after the current transition completes.

Events are the **only** mechanism by which machines influence each other. No machine holds a reference to another machine. No machine calls another machine's methods.

### Subscriptions

A machine class declares which events trigger which transitions via two classmethods:

```python
@classmethod
def subscriptions(cls) -> dict[str, str]:
    """Exact event-name → transition mapping."""
    return {
        "calc.computed": "record_compute",
        "calc.errored": "record_error",
    }

@classmethod
def pattern_subscriptions(cls) -> dict[str, str]:
    """Glob-pattern → transition mapping (fnmatch syntax)."""
    return {
        "calc.memory_*_requested": "record_memory_request",
    }
```

Both are honored at registration. A single event matching multiple patterns or both an exact name and a pattern triggers each handler in turn.

### Telemetry events

Events emitted purely for observation — no machine should subscribe to them — would otherwise be flagged as dead letters by the structural check. Declare them on the emitting machine:

```python
@classmethod
def telemetry_events(cls) -> set[str]:
    return {"trace.span_started", "trace.span_ended"}
```

These names are excluded from the dead-letter list. Subscribing to them still works as usual.

### Timers

Machines schedule delayed events via `self.set_timer(event_name, delay, payload)`:

```python
def on_enter_attempting(self) -> None:
    self.set_timer("retry.timeout", 30.0, {"id": self._instance_id})
```

Timer events appear on the bus when the runner's virtual clock advances past their deadline. The runner attaches the clock to each machine instance during `create()`. Calling `set_timer()` on a machine without an attached clock raises `RuntimeError`.

### Introspection API

The base class exposes these read-only properties:

| Property | Type | Description |
|---|---|---|
| `current_state` | `str` | Name of the current state |
| `is_final` | `bool` | Whether the current state is final |
| `available_transitions` | `list[str]` | Transitions that can fire from the current state |
| `all_states` | `list[str]` | All declared states |
| `all_transitions` | `list[dict]` | All declared transitions with source/target info |
| `context` | `dict` | Read-only copy of the context |
| `history` | `list[TransitionRecord]` | Ordered log of all transitions fired |

### Firing Transitions

```python
result: TransitionResult = machine.fire("validate", **kwargs)
```

`TransitionResult` contains:

```python
@dataclass(frozen=True)
class TransitionResult:
    success: bool
    source: str
    target: str
    transition: str
    failure_reason: str | None
    events_emitted: list[Event]
```

Transitions can fail for exactly three reasons:

1. The transition name doesn't exist → `"unknown_transition"`
2. The transition can't fire from the current state → `"invalid_source_state"`
3. No guard returned True for any branch → `"no_guard_passed"`

### Serialization

Machines are serializable for persistence and replay:

```python
snapshot: dict = machine.snapshot()
# {"state": "validated", "context": {...}, "history": [...]}

machine = OrderLifecycle.restore(snapshot, event_bus=bus, clock=clock)
```

`snapshot()` returns a plain dict with no object references. `restore()` is a classmethod that reconstitutes a machine from a snapshot. These methods enable the persistence adapter and simulation replay.

For whole-runner snapshot/restore covering every instance, the event log, and the clock, see [04-simulation-runner](04-simulation-runner.md).

## Constraints

These constraints are enforced structurally. Violating them is a protocol error caught before simulation begins.

1. Exactly one initial state per machine.
2. At least one final state per machine.
3. No imports outside the standard library and `sddx`.
4. Guards must not mutate context.
5. Side effects must not perform I/O (no network, filesystem, or database calls).
6. All inter-machine communication goes through `self.emit()`.
