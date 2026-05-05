# Event System

## Purpose

The event system is the sole communication channel between state machines. It serves two roles: delivering events between machines during operation, and recording a complete, ordered log of all events for analysis.

## Event Structure

```python
@dataclass(frozen=True)
class Event:
    name: str                  # Dot-namespaced identifier, e.g. "order.validated"
    payload: dict              # Arbitrary data, serializable to JSON
    source_machine: str        # Class name of the emitting machine
    source_instance: str       # Instance identifier (e.g. order_id)
    timestamp: float           # Virtual-clock time, or monotonic fallback
    correlation_id: str        # Groups events from the same originating action
```

Events are immutable. Once created, they cannot be modified. This is essential for replay and debugging.

## Event Naming Convention

Events follow a `{domain}.{action}` pattern:

```
order.validated
order.cancelled
payment.authorized
payment.failed
inventory.reserved
inventory.released
```

The domain prefix matches the machine that emits the event. The action suffix describes what happened, in past tense. **Events are facts about what already occurred, not commands about what should happen.**

## Event Bus

### Interface

```python
class EventBus:
    def emit(self, event: Event) -> None:
        """Record the event and deliver to subscribers."""

    def subscribe(self, event_name: str, handler: Callable[[Event], None]) -> None:
        """Register a handler for events matching the exact name."""

    def subscribe_pattern(self, pattern: str, handler: Callable[[Event], None]) -> None:
        """Register a handler for events matching a glob pattern (fnmatch)."""

    @property
    def log(self) -> EventLog:
        """Read-only access to the complete ordered log of all events emitted."""
```

### Delivery Semantics

Events are delivered **synchronously and in order** within the runner's step cycle. When a machine emits an event during a transition:

1. The event is appended to the log.
2. The event is placed in a delivery queue.
3. After the emitting transition completes (all side effects finish), the queue is drained.
4. Each subscriber receives the event. If a subscriber triggers a transition on another machine, any events that machine emits are added to the queue.
5. The queue drains until empty. One external stimulus can cascade through multiple machines.

This is depth-first, synchronous delivery. It mirrors what would happen in a single-process production deployment and is the simplest model to reason about.

### Cascade Limits

To prevent infinite loops, the bus enforces a maximum cascade depth (default: 20). If an event triggers a chain of reactions that exceeds this depth, the bus halts with `RuntimeError`. This is almost always a modeling error — a circular dependency between machines.

## Subscriptions

Machines declare their subscriptions through two class-level methods:

### Exact subscriptions

```python
class InventoryState(StateMachine):
    available = State(initial=True)
    reserved = State()
    allocated = State()
    released = State(final=True)

    reserve = available.to(reserved)
    allocate = reserved.to(allocated)
    release = (reserved | allocated).to(released)

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {
            "order.validated": "reserve",
            "order.cancelled": "release",
            "order.fulfilled": "allocate",
        }
```

### Pattern subscriptions

For events that share a structural prefix or suffix:

```python
class OperationLog(StateMachine):
    @classmethod
    def pattern_subscriptions(cls) -> dict[str, str]:
        return {
            "calc.memory_*_requested": "record_memory_request",
            "*.timed_out": "record_timeout",
        }
```

Pattern matching uses `fnmatch` syntax (`*`, `?`, `[seq]`). One event can match multiple patterns and exact subscriptions; each handler is invoked in registration order.

Both `subscriptions()` and `pattern_subscriptions()` are honored at registration. The runner wires them up via the EventBus.

## Instance Resolution

When an event arrives for a machine type, the system must determine **which instance** should receive it. This is handled by a resolver function registered with the runner:

```python
runner.register_resolver(
    InventoryState,
    lambda event: event.payload.get("order_id")
)
```

The resolver extracts an instance key from the event payload. The runner looks up an existing instance for that key, or creates one (seeded with the event payload as initial context) if none exists. This is the same mechanism the persistence adapter uses in production.

## Event Log

The event log is the primary artifact used for analysis. It is an append-only, ordered list of all events emitted during a run.

### Log Queries

```python
# All events from a specific machine type
log.filter(source_machine="OrderLifecycle")

# All events with a specific name
log.filter(name="payment.failed")

# All events from a specific instance
log.filter(source_instance="ord_123")

# All events in a correlation group
log.filter(correlation_id="corr_abc123")

# Events within a time window
log.after(t1).before(t2)

# Chained filters
log.filter(source_machine="PaymentFlow").filter(name="payment.failed")

# Unique instance IDs of a machine type
log.unique_instances("OrderLifecycle")
```

### Log as Production Asset

In production, the event log maps directly to an event store or structured logging system. The `Event` dataclass serializes to JSON for durable storage. Because the log format is defined during simulation, the production event store schema is a known quantity before any adapter code is written.

## Dead Letter Detection

During structural analysis, the runner checks that every event emitted by any machine has at least one subscriber (exact or pattern). An event with no subscribers is a **dead letter** — it indicates either a missing machine, a missing subscription, or an event that should not be emitted.

### Telemetry events

Some events exist purely for observation: trace spans, audit hooks, metrics. Declare them on the emitting machine to suppress dead-letter warnings:

```python
class TracedOrder(StateMachine):
    @classmethod
    def telemetry_events(cls) -> set[str]:
        return {"trace.span_started", "trace.span_ended"}
```

Telemetry events are emitted normally — outbound adapters (logging exporters, metrics sinks) can still subscribe to them — but the structural check no longer flags them as missing consumers.

## Event Schema Validation

Event payloads currently follow conventions documented alongside each machine. Schema validation as a first-class framework feature is planned for v0.2; until then, payload contracts are by convention and reviewed during scenario writing.

If you need strict validation today, validate at the adapter boundary or write an invariant that asserts payload shape across the event log.
