# Subscription Billing — sddx example

A working subscription billing system built with sddx. Demonstrates the
features the calculator example doesn't:

- **`sddx.std.Retry`** subclassed for bounded payment retries with exponential backoff.
- **Virtual time** driving the entire failure-recovery flow via `advance_time`.
- **`runner.snapshot()` / `runner.restore()`** preserving an active session across runner restarts, including pending grace-period timers.
- **A "real" outbound adapter** (`FakePaymentProvider`) modeled as a state machine so it participates in simulation lifecycle.
- **Pattern subscriptions** in `OperationLog` for `payment.*` and `retry.*` events.
- **An HTTP inbound adapter** with a browser-based demo UI (vanilla JS + Pico.css), letting you click through scenarios in real time.

## Running it

```bash
# Full convergence (Layer 1 + Layer 2 + invariants)
PYTHONPATH=src python -m sddx -C examples/subscription_billing converge

# Snapshot/restore scenarios (Python rather than YAML)
PYTHONPATH=src:examples/subscription_billing python \
    examples/subscription_billing/scenarios/persistence/test_snapshot_restore.py

# HTTP API + demo UI (default port 8080)
PYTHONPATH=src:examples/subscription_billing python \
    examples/subscription_billing/adapters/inbound/http_api.py
# Then open http://localhost:8080/ in a browser.

# HTTP adapter unit tests (handlers tested without HTTP)
PYTHONPATH=src:examples/subscription_billing pytest \
    examples/subscription_billing/adapters/inbound/test_http_api.py
```

Expected: `9/9 scenarios converged`, 7 invariants × 9 = 63 invariant checks all pass; both snapshot/restore tests pass; 14 adapter tests pass.

## Domain decomposition

```
        ┌────────────────────────┐
        │ SubscriptionLifecycle  │  pending → active → suspended → churned
        │      (one per sub)     │              ↓ ↑          ↓
        └────────────────────────┘           cancelled (final)
                ▲   ▲   ▲
                │   │   │  retry.succeeded / retry.exhausted /
                │   │   │  subscription.grace_expired (timer)
                │   │   │
        ┌───────┴───┴───┴───────┐
        │   PaymentAttempt      │  (subclasses sddx.std.Retry)
        │   one per cycle       │  pending → attempting ⇄ backing_off → succeeded/exhausted
        └───────────────────────┘
                  ▲   │
                  │   │ retry.attempt_requested
       payment.*  │   ▼
        ┌─────────┴───────────────┐
        │   FakePaymentProvider   │  reads context.outcomes, emits payment.captured/declined
        │   (one shared instance) │
        └─────────────────────────┘

  All events also flow into OperationLog (one per subscription) for audit.
```

## Machines

| Machine | Role | Notes |
|---|---|---|
| `SubscriptionLifecycle` | Customer-facing subscription | Schedules a grace timer on entering `suspended`; if it fires before recovery or cancel, churns. |
| `PaymentAttempt` | One billing cycle | Subclasses `sddx.std.Retry`. `SUCCESS_EVENT="payment.captured"`, `FAILURE_EVENT="payment.declined"`. Each cycle is a separate instance keyed `"<sub_id>:cycle<n>"`. |
| `FakePaymentProvider` | Outbound payment adapter | Pops scripted outcomes from context. One shared instance `"provider"` per simulation. In production this would be a non-state-machine adapter calling Stripe/etc. |
| `OperationLog` | Audit trail | Subscribes to everything via pattern subs (`payment.*`, `retry.*`) plus exact subs for subscription events. Closes on `cancelled`/`churned`. |

## Scenarios at a glance

```
scenarios/
├── happy_path/
│   ├── activation_succeeds                   First charge succeeds → active
│   ├── activation_with_retry                 First fails, second succeeds (uses advance_time)
│   ├── cycle_2_completes_after_active        Second cycle's success keeps subscription active
│   ├── cancel_from_active                    User cancels an active subscription
│   └── cancel_from_pending                   User cancels before any charge
├── failure/
│   ├── activation_exhausts_retries           All 3 attempts fail → suspended
│   └── active_then_suspended_after_failure   Active subscription, second cycle fails → suspended
├── time_driven/
│   ├── grace_period_expires_into_churn       Suspended → grace timer (7 days) → churned
│   └── cancel_within_grace_period            Cancel while suspended; grace timer harmlessly fires later
└── persistence/
    └── test_snapshot_restore.py              Python scenarios — snapshot then restore an active sub,
                                              and a suspended sub with a pending grace timer
```

## Resolver wiring

The trickiest part of integrating multi-machine sddx examples is mapping
events back to the right instance. See `sddx_project.py`:

- `SubscriptionLifecycle` and `OperationLog` resolve via `subscription_id` if present in payload, else by extracting the prefix of `retry_id` (which is `"<sub_id>:cycle<n>"`).
- `PaymentAttempt` resolves via `retry_id` directly.
- `FakePaymentProvider` resolves to a constant `"provider"` — one shared instance handles all cycles.

This single-resolver-with-fallback pattern is the trade-off for clean
event-only coupling: the event payload has to carry enough info for every
subscriber to find its instance, but no machine needs a direct reference
to any other.

## HTTP adapter + demo UI

`adapters/inbound/http_api.py` is a thin HTTP wrapper over the runner. It
follows the SDD adapter contract: pure handler functions translate parsed
request payloads into `runner.fire(...)` calls and return `(status, body)`
tuples; an `http.server`-based wrapper turns those into HTTP responses.
Zero external dependencies — uses only the Python standard library plus
[Pico.css](https://picocss.com/) loaded from a CDN for the UI.

### Endpoints

| Method | Path | Handler |
|---|---|---|
| `GET`    | `/subscriptions`                | List all subscriptions with state and cycle count |
| `POST`   | `/subscriptions`                | Create + run first PaymentAttempt |
| `GET`    | `/subscriptions/{id}`           | Read state, context, cycles |
| `DELETE` | `/subscriptions/{id}`           | Fire `cancel` |
| `POST`   | `/subscriptions/{id}/cycles`    | Start the next billing cycle |
| `GET`    | `/events`                       | Recent events (`?since=N&limit=M`) |
| `GET`    | `/clock`                        | Virtual clock state + pending timers |
| `POST`   | `/admin/advance_time`           | Drive `runner.advance_time(seconds)` — the demo's killer feature |
| `GET`    | `/`, `/static/...`              | Demo UI (HTML/JS/CSS) |

### What the UI shows

- Cards for each subscription with current state, plan, amount, and cycle count.
- Per-card actions: **Bill now** (start a new cycle), **Cancel**.
- A **virtual clock** panel: current `t`, pending-timer count, and buttons for `+1 min / +1 hour / +1 day / +7 days` plus a custom `N seconds` input. This is the demo's centerpiece — most demos can't visualize "skip ahead 7 days and watch the suspended subscription churn."
- An **event log** scrolling the last 30 events with virtual timestamps.

The page polls the API once per second (no WebSockets, no SSE) so updates
appear within ~1s of any change.

### Adapter design notes

- The `outcomes` field on `POST /subscriptions` and `POST /cycles` is an
  array of `"succeed" | "decline"` that gets appended to the
  `FakePaymentProvider`'s scripted-response queue. This is fixture-style
  injection — useful for the demo, replaced in production by a real
  payment-processor adapter.
- The shared `FakePaymentProvider("provider")` instance is created once
  during runner construction. Cycle instance ids encode subscription +
  cycle number (`sub_1:cycle3`); the resolver in `sddx_project.py` maps
  events back to the right machine.
- Adapter handlers are tested as pure functions in
  [test_http_api.py](adapters/inbound/test_http_api.py) — 14 tests, no HTTP roundtrips.

## What this example demonstrates that the calculator doesn't

| Feature | Where to look |
|---|---|
| Subclassing `sddx.std.Retry` | [machines/payment_attempt.py](machines/payment_attempt.py) |
| `advance_time` driving real timeouts | All scenarios under `failure/` and `time_driven/` |
| `set_timer` from `on_enter_*` for grace period | [machines/subscription_lifecycle.py](machines/subscription_lifecycle.py) — `on_enter_suspended` |
| Multi-instance machines (one PaymentAttempt per cycle) | [scenarios/happy_path/cycle_2_completes_after_active.yaml](scenarios/happy_path/cycle_2_completes_after_active.yaml) |
| Pattern subscriptions | [machines/operation_log.py](machines/operation_log.py) |
| Outbound adapter as a state machine | [machines/fake_payment_provider.py](machines/fake_payment_provider.py) |
| Resolver-with-fallback for cross-cutting events | [sddx_project.py](sddx_project.py) |
| Whole-runner snapshot/restore | [scenarios/persistence/test_snapshot_restore.py](scenarios/persistence/test_snapshot_restore.py) |
| HTTP inbound adapter | [adapters/inbound/http_api.py](adapters/inbound/http_api.py) |
| Browser-based demo UI | [adapters/inbound/static/index.html](adapters/inbound/static/index.html) |
