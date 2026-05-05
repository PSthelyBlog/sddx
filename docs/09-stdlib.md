# Standard Library Machines

The `sddx.std` package ships opt-in machines for patterns that recur across domains. Each is a normal `StateMachine` — you subscribe to its events and emit the events it consumes, same as any machine you write yourself.

```python
from sddx.std import Barrier, Retry
```

Currently shipped:

- [`Barrier`](#barrier) — fan-in over N events sharing a correlation_id.
- [`Retry`](#retry) — bounded retry-with-exponential-backoff over a single operation.

More are planned (see [README.md](../README.md#status)).

---

## `Barrier`

A parent task fans out N work items, each producing a completion event. The Barrier waits until all N complete (or its timeout fires), then emits a single `barrier.completed` or `barrier.timed_out` event.

### Subclass to declare the watched event

```python
from sddx.std import Barrier

class ToolBatchBarrier(Barrier):
    WATCHED_EVENT = "tool.completed"
```

### State machine

```
       start
pending ────▶ waiting ────────────────▶ completed (final)
                │ ▲    record_completion (count == expected)
                │ │
                │ └─ record_completion (count < expected, self-loop)
                │
                └────▶ timed_out (final)
                       fire_timeout (driven by timer)
```

| State | Meaning |
|---|---|
| `pending` | Initial. Nothing scheduled yet. |
| `waiting` | Counting completions; timer (if any) is running. |
| `completed` | All expected events received. Final. |
| `timed_out` | Timer fired before all events arrived. Final. |

### Lifecycle

1. **Parent creates the instance** with context:
   ```python
   runner.create(ToolBatchBarrier, "B1", {
       "barrier_id": "B1",
       "expected": 5,
       "received": 0,
       "timeout_seconds": 30.0,    # or None for no timeout
   })
   ```
2. **Parent fires `start()`** — this schedules the timer (if any) and moves to `waiting`.
3. Each `WATCHED_EVENT` self-loops on `waiting`, incrementing `received`. When `received == expected`, the next event transitions to `completed`.
4. If the timer fires first, the barrier transitions to `timed_out`.

### Events emitted

| Event | When | Payload |
|---|---|---|
| `barrier.completed` | All expected items received | `{barrier_id, received}` |
| `barrier.timed_out` | Timer expired before completion | `{barrier_id, received, expected}` |
| `barrier.timeout_fired` | Internal: scheduled timer firing | `{barrier_id}` |

The first two are what you typically subscribe to in a parent machine. `barrier.timeout_fired` is internal to the Barrier; it triggers the `fire_timeout` transition.

### Example: parallel tool calls

```python
class AgentSession(StateMachine):
    awaiting_tools = State()
    integrating = State()

    tool_batch_done = awaiting_tools.to(integrating)

    @classmethod
    def subscriptions(cls):
        return {"barrier.completed": "tool_batch_done"}
```

When the agent dispatches N tool calls with `correlation_id="batch_42"`, it also creates a `ToolBatchBarrier("batch_42")` with `expected=N`. As each tool emits `tool.completed`, the barrier counts down. When complete, it emits `barrier.completed`, which the agent's `awaiting_tools` state subscribes to.

---

## `Retry`

A bounded retry-with-exponential-backoff over an operation that may transiently fail. Each attempt is a transition; the timer schedules the next one.

### Subclass to declare success/failure events

```python
from sddx.std import Retry

class HTTPRetry(Retry):
    SUCCESS_EVENT = "http.response_received"
    FAILURE_EVENT = "http.request_failed"
```

### State machine

```
        start
pending ────▶ attempting ─────▶ succeeded (final)
              │  ▲    record_success
              │  │
              │  │ retry_now (after backoff_elapsed timer)
              │  │
              ▼  │
           backing_off
              ▲
              │ record_failure (attempts < max)
              │
              └─ record_failure (attempts >= max) ─▶ exhausted (final)
```

| State | Meaning |
|---|---|
| `pending` | Initial. Configured but not yet running. |
| `attempting` | An attempt is in progress; emitted `retry.attempt_requested` for the workload. |
| `backing_off` | An attempt failed; waiting for backoff timer. |
| `succeeded` | An attempt succeeded. Final. |
| `exhausted` | Reached `max_attempts` without success. Final. |

### Lifecycle

1. **Create the instance** with context:
   ```python
   runner.create(HTTPRetry, "R1", {
       "retry_id": "R1",
       "max_attempts": 3,
       "base_delay": 1.0,    # seconds before the second attempt
       "attempts": 0,
   })
   ```
2. **Fire `start()`** — moves to `attempting`. Emits `retry.attempt_requested` so the workload can act.
3. **Workload responds** with `SUCCESS_EVENT` or `FAILURE_EVENT`.
4. On success: `succeeded`, `retry.succeeded` emitted.
5. On failure with attempts remaining: `backing_off`, timer schedules `retry.backoff_elapsed`. When the timer fires, the Retry transitions back to `attempting`.
6. On failure with `attempts == max_attempts`: `exhausted`, `retry.exhausted` emitted.

### Backoff schedule

```
delay = base_delay * (2 ** (attempt - 2)) for attempt > 1, else base_delay
```

For `base_delay=1.0`: 1s, 2s, 4s, 8s, ...

### Events emitted

| Event | When | Payload |
|---|---|---|
| `retry.attempt_requested` | Each entry to `attempting` | `{retry_id, attempt}` |
| `retry.succeeded` | Reached `succeeded` | `{retry_id, attempts}` |
| `retry.exhausted` | Reached `exhausted` | `{retry_id, attempts}` |
| `retry.backoff_elapsed` | Internal: backoff timer firing | `{retry_id}` |

### Example: payment retry

```python
class BillingCycle(StateMachine):
    charging = State()
    paid = State(final=True)
    failed = State(final=True)

    on_paid = charging.to(paid)
    on_failed = charging.to(failed)

    @classmethod
    def subscriptions(cls):
        return {
            "retry.succeeded": "on_paid",
            "retry.exhausted": "on_failed",
        }
```

The actual payment workload subscribes to `retry.attempt_requested` and emits `payment.succeeded` / `payment.failed`, which the `PaymentRetry` subclass consumes.

---

## When NOT to use the stdlib

These machines exist because the patterns recur. Don't reach for them when:

- **Your "fan-in" is two events.** Just write a small machine with two transitions.
- **Your "retry" is unconditional, single-attempt.** Just emit the operation event again from the failure handler.
- **You need behavior the stdlib doesn't provide.** Bespoke domain logic — say, a barrier that completes early when a critical event arrives — should be a domain machine, not a hacked stdlib subclass.

The stdlib trades a little flexibility for repeatable correctness on common shapes. Use it where the shape fits; write your own where it doesn't.
