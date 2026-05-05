# Architecture

## Overview

sddx produces systems with three distinct layers. The inner layer is built during simulation and never changes. The outer layers are added afterward to connect the system to the real world.

```
┌─────────────────────────────────────────────────┐
│                 Inbound Adapters                │
│          (HTTP, CLI, queue consumers)           │
├─────────────────────────────────────────────────┤
│                                                 │
│              State Machine Core                 │
│                                                 │
│   ┌───────────┐  events  ┌───────────┐         │
│   │ Machine A ├─────────▶│ Machine B │         │
│   └───────────┘          └─────┬─────┘         │
│                                │ events         │
│                          ┌─────▼─────┐         │
│                          │ Machine C │         │
│                          └───────────┘         │
│                                                 │
├─────────────────────────────────────────────────┤
│                Outbound Adapters                │
│        (databases, APIs, email, queues)         │
└─────────────────────────────────────────────────┘
```

## Layer Definitions

### State Machine Core

This is the production core. It contains all domain logic expressed as state machines communicating through an event bus. This layer has **zero infrastructure dependencies** — no imports of database drivers, HTTP libraries, or framework code. It depends only on `sddx` and Python's standard library.

The core is built and verified entirely during the simulation phase. The runner operates it directly, feeding synthetic inputs and observing transitions and events. Everything discovered during simulation — missing states, unhandled transitions, failure paths, timer-driven retries — is fixed here before any adapter is written.

### Inbound Adapters

Inbound adapters translate external signals into state machine transitions. An HTTP endpoint receives a request, extracts the relevant data, locates the appropriate machine instance, and calls a transition. A queue consumer reads a message and does the same.

Inbound adapters contain no domain logic. They perform three tasks: deserialize input, locate a machine instance, and invoke a transition. If an adapter contains an `if` statement about business rules, that logic belongs in a machine guard instead.

### Outbound Adapters

Outbound adapters subscribe to machine events and side-effect state entries, then perform infrastructure operations. When a machine enters a state or emits an event, an outbound adapter might persist the state to a database, send an email, or publish to a message queue.

Outbound adapters are invisible to the state machines. A machine emits an event; it does not know or care what listens. This means the simulation environment can run the full core without any outbound adapters present.

### Persistence Adapter (Special Case)

The persistence adapter deserves separate mention because it spans both directions. It handles serializing machine state to durable storage on state changes (outbound) and rehydrating machine instances from storage on startup or when a new transition arrives for an existing process (inbound).

sddx supports two granularities:

- **Per-machine** via the protocol's `snapshot()` / `restore()` methods on `StateMachine`. Pure data transformations, not I/O.
- **Whole-runner** via `runner.snapshot()` / `runner.restore()`, which captures every instance, the event log, and the virtual clock as a single JSON-serializable dict.

The persistence adapter calls them and handles the actual storage.

## The framework's own components

```
                    ┌──────────────────────┐
                    │  StateMachine (base) │ ◄── protocol.py
                    │  + State / Transition│
                    └──────────┬───────────┘
                               │
                               ▼
   ┌───────────────┐    ┌──────────────┐    ┌─────────────┐
   │  EventBus     │◄──►│ SimulationRunner│◄──►│ VirtualClock│
   │  (events.py)  │    │ (runner.py)  │    │ (timing.py) │
   └───────────────┘    └──────┬───────┘    └─────────────┘
                               │
                  ┌────────────┼─────────────┐
                  ▼            ▼             ▼
          ┌────────────┐ ┌──────────┐ ┌──────────────┐
          │ Scenario   │ │Invariants│ │ std/ machines│
          │ Runner     │ │          │ │ Barrier,Retry│
          └────────────┘ └──────────┘ └──────────────┘
```

- **protocol.py** — the only thing your machine modules import. State, StateMachine, hooks, guards.
- **events.py** — the bus. Exact + glob subscriptions; cascade-depth protection.
- **runner.py** — operating environment. Registers machines, owns the bus and the clock, drives transitions, runs structural checks, snapshots/restores entire simulation state.
- **timing.py** — virtual clock. Replaces wall-clock time; drives `advance_time` in scenarios.
- **scenario.py** — YAML parser + executor. Implements `create / fire / advance_time / assert` step types and the final `expect` block.
- **invariants.py** — cross-machine property checks expressed as Python assertions over `(EventLog, states_dict)`.
- **std/** — opt-in machines: `Barrier` (fan-in by correlation), `Retry` (bounded backoff). See [09-stdlib](09-stdlib.md).
- **__main__.py** — `python -m sddx` CLI; auto-discovers `machines/`, `scenarios/`, `invariants/` under a project root. See [10-cli](10-cli.md).

## Deployment Topology

The architecture is deliberately agnostic about deployment. The same state machine core can run in:

- A single process (all machines in memory, event bus is synchronous)
- Multiple processes (machines partitioned by type, event bus backed by a message queue)
- Serverless functions (each machine instance rehydrated from storage per invocation)
- Long-running sessions paused and resumed across process restarts via `runner.snapshot()` / `runner.restore()`

The I/O adapters change; the core does not. This is the central architectural guarantee.

## Project directory structure

```
project/
├── machines/              # State Machine Core
│   ├── __init__.py
│   ├── order_lifecycle.py
│   ├── payment_flow.py
│   └── inventory_state.py
├── adapters/              # I/O Adapters
│   ├── inbound/
│   │   ├── http_api.py
│   │   └── queue_consumer.py
│   └── outbound/
│       ├── persistence.py
│       ├── notifications.py
│       └── external_apis.py
├── scenarios/             # YAML scenarios, auto-discovered by the CLI
│   ├── happy_path/
│   └── failure/
├── invariants/            # Cross-machine property checks
├── tests/                 # Adapter tests (core is tested via simulation)
│   ├── test_inbound/
│   └── test_outbound/
└── sddx_project.py        # Optional: register_resolvers(runner) hook for the CLI
```

`machines/`, `scenarios/`, and `invariants/` have no dependency on `adapters/`. The dependency arrow points inward only.
