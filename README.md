# sddx — Simulation-Driven Development, eXtended

A backward-compatible superset of [SDD](https://github.com/anthropics/simulation-driven-development) (Simulation-Driven Development).
Same convergence philosophy — structural checks, scenario coverage, property invariants — with cleaner ergonomics, real timer support, runner-level snapshot/restore, and an opt-in machine stdlib.

## Quick start

```python
from sddx import State, StateMachine

class OrderLifecycle(StateMachine):
    created = State(initial=True)
    validated = State()
    completed = State(final=True)
    cancelled = State(final=True)

    validate = created.to(validated) | created.to(cancelled)
    complete = validated.to(completed)
    cancel = (created | validated).to(cancelled)

    def guard_validate_to_validated(self, **kw):
        return bool(self._context.get("items"))

    def on_enter_validated(self, source: str):
        self.emit("order.validated", {"order_id": self._context["order_id"]})
```

Run convergence:

```bash
sddx -C my-project converge
```

## Documentation

Read in order if new to sddx:

| # | Doc | Topic |
|---|---|---|
| 00 | [Introduction](docs/00-introduction.md) | What sddx is, how to navigate the docs |
| 01 | [Architecture](docs/01-architecture.md) | Three-layer system structure |
| 02 | [State Machine Protocol](docs/02-state-machine-protocol.md) | The contract every machine must satisfy |
| 03 | [Event System](docs/03-event-system.md) | Inter-machine communication, pattern subs, telemetry |
| 04 | [Simulation Runner](docs/04-simulation-runner.md) | Operating machines, virtual time, snapshot/restore |
| 05 | [Convergence Criteria](docs/05-convergence-criteria.md) | The three layers and what each verifies |
| 06 | [I/O Adapters](docs/06-io-adapters.md) | Connecting the converged core to infrastructure |
| 07 | [Workflow Guide](docs/07-workflow-guide.md) | The end-to-end process, including subagent dispatch |
| 08 | [Scenario Language](docs/08-scenario-language.md) | YAML grammar for scenarios |
| 09 | [Standard Library](docs/09-stdlib.md) | `Barrier` and `Retry` machines |
| 10 | [CLI](docs/10-cli.md) | `python -m sddx` reference |
| — | [Migration](docs/migration.md) | Porting from vanilla SDD |

## What sddx adds over vanilla SDD

| Affordance | vanilla SDD | sddx |
|---|---|---|
| Self-loop transitions | `a.to(a) \| b.to(b) \| ...` | `(a \| b).loop()` |
| Knowing the source state in a hook | inspect `self._history` (broken on `on_enter`) | `def on_enter_X(self, source: str)` |
| Per-source guards | re-check `self._current_state` inside | `guard_T_from_SOURCE_to_TARGET` |
| Pattern event subscriptions | latent in `EventBus`, unused | `pattern_subscriptions()` classmethod |
| Telemetry-only events | flag as dead letters | `telemetry_events()` classmethod |
| Virtual clock / timeouts | stub raises a warning | `runner.advance_time(s)` + `self.set_timer(...)` |
| Working `advance_time` in scenarios | not implemented | drives `VirtualClock` |
| Working context assertions in scenarios | not implemented | compares `instance.context` fields |
| Snapshot/restore | per-machine only | runner-level (instances + log + clock) |
| Project CLI | hand-rolled per project | `python -m sddx check\|run\|converge` |
| Stdlib machines | none | `sddx.std.Barrier`, `sddx.std.Retry` |

Vanilla SDD machines run unmodified.

## Layout

```
src/sddx/
├── protocol.py     # State, StateMachine, hooks, guards
├── events.py       # Event, EventBus, EventLog
├── runner.py       # SimulationRunner, snapshot/restore
├── scenario.py     # Scenario YAML parser + ScenarioRunner
├── invariants.py   # Cross-machine property checks
├── timing.py       # VirtualClock
├── std/            # Opt-in machine stdlib
└── __main__.py     # python -m sddx CLI
examples/calculator/  # Canonical end-to-end demo (14 scenarios, 9 invariants)
docs/                 # Documentation, indexed above
tests/                # Framework unit tests (49 tests)
```

## Running the calculator example

```bash
cd ./sddx
PYTHONPATH=src python -m sddx -C examples/calculator converge
```

Expected output: `14/14 scenarios converged`, with 9 invariants passing each.

## Status

- v0.1: Phase 1 ergonomics + Phase 2 capability (timers, snapshot/restore, context assertions, CLI). 49 framework tests passing. Calculator example fully converges.
- v0.2 (planned): event-payload schema validation, more stdlib machines (`RequestResponse`, `Budget`, `MockAdapter`).
- Future: `sdd_agent` package for LM-Studio-class workloads. Separate repo, depends on sddx.
