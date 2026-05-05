# I/O Adapter Specification

## Purpose

I/O adapters connect the production-core state machines to real infrastructure. They are written **after convergence**, are deliberately thin, and contain no domain logic. Their existence is the reason the state machine core can remain pure.

## Adapter Taxonomy

### Inbound Adapters

Inbound adapters translate external signals into machine transitions.

```
External Signal ──▶ Inbound Adapter ──▶ runner.fire(instance, Machine, transition, **args)
```

An inbound adapter does exactly three things:

1. **Deserialize** the external input into a Python dict.
2. **Resolve** which machine instance should receive the action (e.g., extract `order_id` from a request path).
3. **Fire** the appropriate transition on the runner with the extracted arguments.

```python
# Example: FastAPI inbound adapter
@app.post("/orders/{order_id}/validate")
async def validate_order(order_id: str, body: ValidateRequest):
    result = runner.fire(order_id, OrderLifecycle, "validate",
                         items=body.items, customer_id=body.customer_id)
    if result.errors:
        raise HTTPException(400, detail=result.errors[0].message)
    return {"status": "ok"}
```

**The adapter does not decide whether validation succeeds.** That's the machine's guard. The adapter just passes data through.

If an inbound adapter contains conditional logic about business rules, that logic has escaped the core and must be moved into a machine guard.

### Outbound Adapters

Outbound adapters subscribe to machine events and perform external side effects.

```
Machine event ──▶ Event Bus ──▶ Outbound Adapter ──▶ External System
```

Outbound adapters register as event subscribers, just like machines do, but instead of firing transitions, they perform I/O:

```python
# Example: Persistence outbound adapter (using snapshot/restore at machine level)
class PersistenceAdapter:
    def __init__(self, db, runner):
        runner.event_bus.subscribe_pattern("*", self.on_any_event)
        self._runner = runner
        self._db = db

    def on_any_event(self, event: Event):
        # Snapshot the source machine after every event.
        machine_class = self._runner.get_machine_class(event.source_machine)
        if machine_class is None:
            return
        machine = self._runner.get(machine_class, event.source_instance)
        if machine is None:
            return
        self._db.upsert(
            table="machine_states",
            key=(event.source_machine, event.source_instance),
            data=machine.snapshot(),
        )

# Example: Notification outbound adapter
class NotificationAdapter:
    def __init__(self, email_service, runner):
        runner.event_bus.subscribe("order.completed", self.on_order_completed)
        runner.event_bus.subscribe("payment.failed", self.on_payment_failed)
        self._email = email_service

    def on_order_completed(self, event: Event):
        self._email.send(
            to=event.payload["customer_email"],
            template="order_complete",
            data=event.payload,
        )

    def on_payment_failed(self, event: Event):
        self._email.send(
            to=event.payload["customer_email"],
            template="payment_failed",
            data=event.payload,
        )
```

For high-volume systems, prefer **runner-level** snapshots over per-machine when a single durable store is sufficient — see [04-simulation-runner](04-simulation-runner.md#snapshot--restore).

### Persistence Adapter (Bidirectional)

The persistence adapter is unique because it operates in both directions:

**Outbound (save):** After state changes, serialize and store machine snapshots (per-machine or whole-runner).

**Inbound (load):** When a transition arrives for an instance that isn't in memory, rehydrate it from storage using `Machine.restore(snapshot, event_bus=..., clock=...)`.

```python
class PersistenceAdapter:
    def __init__(self, db, runner):
        self._db = db
        self._runner = runner
        runner.event_bus.subscribe_pattern("*", self.save_snapshot)

    def hydrate_if_missing(self, machine_class, instance_id):
        """Call this from inbound adapters before firing a transition."""
        if self._runner.get(machine_class, instance_id) is not None:
            return
        row = self._db.get(
            table="machine_states",
            key=(machine_class.__name__, instance_id),
        )
        if row is None:
            return
        machine = machine_class.restore(
            row["data"],
            event_bus=self._runner.event_bus,
            instance_id=instance_id,
            clock=self._runner.clock,
        )
        self._runner._instances[(machine_class, instance_id)] = machine

    def save_snapshot(self, event: Event):
        machine_class = self._runner.get_machine_class(event.source_machine)
        if machine_class is None:
            return
        machine = self._runner.get(machine_class, event.source_instance)
        if machine is None:
            return
        self._db.upsert(
            table="machine_states",
            key=(event.source_machine, event.source_instance),
            data=machine.snapshot(),
        )
```

## Adapter Design Rules

### Rule 1: No Domain Logic

An adapter must not contain `if` statements that evaluate business rules. All of the following belong in machine guards or side effects, not adapters:

- Whether an order can be cancelled at this stage
- Whether a payment amount is valid
- Whether inventory is sufficient
- Whether a customer is authorized

The adapter may contain infrastructure-level conditionals (retry logic, circuit breaking, format validation) but never domain-level ones.

### Rule 2: Adapters Are Replaceable

The state machine core must function identically regardless of which adapters are attached. Swap FastAPI for a CLI adapter, swap Postgres for SQLite, swap email for SMS — the core's behavior does not change.

This is testable: the simulation runner is proof that the core works with no adapters at all.

### Rule 3: Adapters Fail Gracefully

Adapter failures must not corrupt machine state. If a persistence write fails, the in-memory machine state is still correct. If a notification fails, the order's state is unaffected.

This means adapters should handle their own error recovery (retries, dead letter queues, circuit breakers) without propagating failures back into the state machine core. For retry-with-backoff in domain code, prefer modeling it as a machine — see [09-stdlib](09-stdlib.md#retry).

### Rule 4: Adapters Are Independently Testable

Each adapter is tested against a mock or test instance of its external dependency (a test database, a mock email service). These tests verify the adapter's wiring, not the domain logic.

Adapter tests are small and mechanical:

- Does the HTTP adapter correctly extract `order_id` from the path?
- Does the persistence adapter correctly serialize and deserialize snapshots?
- Does the notification adapter send the right template for each event?

Domain correctness is already proven by simulation. Adapter tests only verify the plumbing.

## Production Runner

In production, the simulation runner is the same `SimulationRunner` class, with adapters wired in at startup:

```python
def build_production_runner() -> SimulationRunner:
    runner = SimulationRunner()
    for machine_class in [OrderLifecycle, PaymentFlow, InventoryState]:
        runner.register(machine_class)
    runner.register_resolver(...)

    # Attach adapters
    db = postgres.connect(...)
    persistence = PersistenceAdapter(db, runner)
    NotificationAdapter(email_service, runner)
    HTTPAdapter(app, runner, persistence)
    return runner
```

The same `fire()`, event delivery, and machine management logic is used in simulation and production. Only the adapter wiring differs.

## Adapter Development Workflow

1. **Core converges** — `python -m sddx -C my-project converge` exits 0.
2. **Inventory I/O needs** — what external signals come in? What side effects go out? What must be persisted?
3. **Write adapters** — one adapter per external system, following the rules above.
4. **Test adapters independently** — against mocks, not the full system.
5. **Integration test** — the production runner with all adapters attached processes the same scenarios from convergence testing, now with real (or containerized) infrastructure.

The integration test should produce the same machine states and events as the simulation. If it doesn't, the bug is in an adapter, not the core.
