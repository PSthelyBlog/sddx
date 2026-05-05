# CLAUDE.md

## Project

This is the **sddx** (Simulation-Driven Development, eXtended) framework. State machines are the production core — they are built and verified through simulation, then connected to infrastructure via thin I/O adapters. sddx is a backward-compatible superset of vanilla SDD with cleaner ergonomics, real timer support, runner-level snapshot/restore, and an opt-in machine stdlib.

Read the full documentation in `docs/` (00–10 + migration guide) before making architectural decisions.

## Repository Structure

```
sddx/
├── CLAUDE.md
├── README.md
├── pyproject.toml                  # Python packaging (Python ≥3.11, PyYAML)
├── LICENSE
├── docs/
│   ├── 00-introduction.md          # Entry point — read first
│   ├── 01-architecture.md          # System layers and framework components
│   ├── 02-state-machine-protocol.md # Base class contract, hooks, guards, .loop()
│   ├── 03-event-system.md          # Inter-machine events, pattern subs, telemetry
│   ├── 04-simulation-runner.md     # Operating, observing, advance_time, snapshot/restore
│   ├── 05-convergence-criteria.md  # The three layers
│   ├── 06-io-adapters.md           # Connecting to infrastructure
│   ├── 07-workflow-guide.md        # Phase-by-phase process
│   ├── 08-scenario-language.md     # YAML grammar (advance_time, context assertions)
│   ├── 09-stdlib.md                # Barrier, Retry reference
│   ├── 10-cli.md                   # `python -m sddx` reference
│   └── migration.md                # Porting from vanilla SDD
├── src/sddx/                       # Framework source code
│   ├── __init__.py                 # Public API re-exports
│   ├── __main__.py                 # `python -m sddx` CLI
│   ├── protocol.py                 # State, StateMachine, Transition, hooks, guards
│   ├── events.py                   # Event, EventBus, EventLog
│   ├── runner.py                   # SimulationRunner + snapshot/restore
│   ├── scenario.py                 # ScenarioParser, ScenarioRunner
│   ├── invariants.py               # InvariantReport, check_invariants
│   ├── timing.py                   # VirtualClock, ScheduledEvent
│   └── std/                        # Opt-in stdlib machines
│       ├── barrier.py              # Fan-in by correlation_id
│       └── retry.py                # Bounded retry-with-backoff
├── tests/                          # Framework unit tests (49 tests)
└── examples/calculator/            # Canonical end-to-end demo
    ├── machines/                   # Domain state machines
    ├── adapters/                   # I/O adapters
    ├── scenarios/                  # YAML scenarios (happy_path/, failure/)
    ├── invariants/                 # Property invariants
    └── sddx_project.py             # Project config: register_resolvers(runner)
```

The framework code itself (`src/sddx/`) has unit tests in `tests/`. Domain machines built on top of sddx are tested via simulation, not unit tests — that's the whole point.

## Core Principles

These are non-negotiable. Every decision must be consistent with them.

1. **State machines are the production core.** They are not prototypes, not models, not scaffolding. The code built during simulation ships unchanged.
2. **Zero infrastructure imports in machines.** A machine module imports only `sddx` and the Python standard library. No database drivers, no HTTP libraries, no framework code. Ever.
3. **Events are the only inter-machine coupling.** No machine holds a reference to another machine. No machine calls another machine's methods. Communication is exclusively through `self.emit()` and the event bus.
4. **Failures are explicit states.** Error handling is modeled as states and transitions, not try/except blocks. If something can fail, there is a state for it and a transition to it.
5. **Adapters contain no domain logic.** If an adapter has an `if` statement evaluating a business rule, that logic belongs in a machine guard.
6. **Convergence before adaptation.** No adapter code is written until all three convergence layers pass: structural completeness, scenario coverage, property invariants.

## Working with State Machines

### Creating a Machine

Follow the protocol in `docs/02-state-machine-protocol.md`. Every machine must have:

- Exactly one `State(initial=True)`.
- At least one `State(final=True)`.
- Guards named `guard_{transition}_to_{target}` (or `guard_{transition}_from_{source}_to_{target}` for per-source disambiguation), pure (no I/O, no context mutation).
- Side effects (`on_enter_*`, `on_exit_*`, `on_transition_*`) that communicate only via `self.emit()`. They may declare optional `source: str` and/or `target: str` parameters; the framework injects them when the signature accepts them.
- A `subscriptions()` classmethod declaring which exact event names trigger which transitions.
- Optionally `pattern_subscriptions()` for glob-pattern subscriptions (e.g. `"calc.memory_*_requested"`).
- Optionally `telemetry_events()` to declare events that are observation-only (suppresses dead-letter warnings).
- Serialization support via `snapshot()` and `restore()` (inherited from `StateMachine`).

### Guard Rules

Guards return `bool`. They read `self._context` and `**kwargs`. They must not:

- Mutate `self._context`.
- Perform any I/O.
- Access any external state.
- Call `self.emit()`.

When a transition has multiple branches sharing a target, prefer per-source guards (`guard_T_from_SOURCE_to_TARGET`) over inspecting `self._current_state` inside a target-only guard.

### Hook Rules

- Use `on_transition_*` for state-mutating side effects and emissions tied to a specific transition. Self-loops re-fire `on_enter_*` even though state didn't change — emissions belonging to a particular transition belong on the transition hook, not the state-entry hook.
- Use `on_enter_*` only when the emission semantically marks the entry into a state (e.g. `closed`, `error`).
- Within hooks, `self._current_state` reads as the source during `on_exit_*`/`on_transition_*` and as the target during `on_enter_*`. Prefer the explicit `source: str` / `target: str` parameters.

### Event Naming

Events use `{domain}.{past_tense_action}` format: `order.validated`, `payment.failed`, `inventory.released`. Events are facts about what happened, not commands. The domain prefix matches the emitting machine.

### Context Updates

Keyword arguments passed to `machine.fire("transition", **kwargs)` merge into `self._context`. This is the only mechanism for feeding new data into a machine.

**Watch for kwarg/context collisions.** If you subscribe to an event whose payload key shadows an internal context field, the merge will silently overwrite it. Choose disjoint key names — e.g., a machine that holds memory under `context["memory"]` should read incoming requests as `value`, not `memory`.

### Timers

Machines schedule delayed events via `self.set_timer(event_name, delay, payload)`:

```python
def on_transition_charge(self):
    self.set_timer("payment.timeout", 24 * 3600, {"order_id": self._instance_id})
```

The runner attaches a `VirtualClock` to each instance during `create()`. Calling `set_timer()` without an attached clock raises `RuntimeError`. Scenarios drive virtual time via `advance_time:` — see `docs/08-scenario-language.md`.

## Working with the Simulation Runner

### Running Convergence

```bash
# Structural checks only (Layer 1)
python -m sddx -C my-project check

# Run all scenarios (Layer 2; no invariants)
python -m sddx -C my-project run

# Run a specific scenario
python -m sddx -C my-project run scenarios/happy_path/simple_addition.yaml

# Full convergence (Layers 1+2+3)
python -m sddx -C my-project converge
```

`-C PATH` sets the project root for auto-discovery (defaults to current directory). The CLI auto-loads `machines/`, `scenarios/`, `invariants/`, and an optional `sddx_project.py` that exposes `register_resolvers(runner)`. See `docs/10-cli.md`.

### Convergence Checks

Always run convergence in order. Do not skip layers.

1. **`check`** — structural analysis (reachability, termination, dead letters, phantom subscriptions). Telemetry-marked events are exempt from dead-letter checks.
2. **`run`** — scenario execution (transition/state/branch/event coverage). No invariants.
3. **`converge`** — full check including property invariants.

If structural checks fail, fix them before running scenarios. If scenarios fail, fix them before checking invariants.

### Programmatic API

For finer control:

```python
from sddx import SimulationRunner, ScenarioParser, ScenarioRunner

runner = SimulationRunner()
runner.register(MyMachine)
runner.register_resolver(MyMachine, lambda e: e.payload["id"])

scen_runner = ScenarioRunner(runner)
scenarios = ScenarioParser().parse_directory("scenarios")
results = scen_runner.run_all(scenarios, invariants=[my_invariant])
```

The CLI is a convenience over this API, not a separate code path.

### Snapshot / Restore

Capture full simulation state across process restarts:

```python
snap: dict = runner.snapshot()  # JSON-serializable
# ... persist anywhere ...
fresh.restore(snap)             # rebuild instances + log + clock
```

The destination runner must already have the same machine classes registered.

## Subagent Workflow

When dispatching subagents to implement machines:

1. **Give each subagent a brief** containing: machine name, purpose, states, transitions, events to emit (with payload schemas), events to consume, context fields, guard hints, and stdlib hints (e.g., "use `sddx.std.Retry` for the backoff schedule"). See `docs/07-workflow-guide.md` Phase 1 for the brief format.
2. **Subagents work independently.** They do not see each other's code. Integration is your responsibility.
3. **Validate deliverables immediately.** Run structural checks on each returned machine before assembling. Catch protocol violations early.
4. **Assembly is where integration issues surface.** Schema mismatches, dead letters, and phantom subscriptions appear when machines are registered together. Send targeted fix requests — include the failing machine, the scenario, expected vs. actual behavior, and the relevant event log excerpt.

## Diagnosis Sequence

When a scenario fails, follow this order:

1. Read the `StepResult` — which step failed and what was the error type?
2. `no_guard_passed` → Is the guard too strict, or is the scenario feeding bad data? Check both target-only and per-source guard candidates.
3. Unexpected state → Trace the event log to find the cascade that led there.
4. Missing event → Did the transition that should emit it actually fire? If yes, which hook should have emitted it (likely `on_transition_*` rather than `on_enter_*` if the transition is a self-loop)?
5. Timer didn't fire → Did the scenario `advance_time` past the deadline? Is the clock wired (machine created via `runner.create()` not directly)?
6. Cascade issue → Check the event log for circular patterns or unexpected pattern-subscription matches.

Fix, then rerun the failing scenario AND all previously passing scenarios (regression).

## File Conventions

- Machine files: `machines/{domain_name}.py` — one machine per file, class name is PascalCase.
- Event schemas: defined in each machine file alongside the machine class as docstrings (Pydantic schema validation is planned for v0.2).
- Scenarios: `scenarios/{category}/{descriptive_name}.yaml`.
- Invariants: `invariants/{domain_or_cross_cutting}.py` — one function per invariant, named `invariant_*`.
- Adapter files: `adapters/inbound/{interface}.py`, `adapters/outbound/{system}.py`.
- Project config: `sddx_project.py` at the project root — exposes `register_resolvers(runner)`.

## What Not to Do

- Do not put domain logic in adapters. If you're writing a business rule outside `machines/`, stop.
- Do not create machines without final states. Every process terminates.
- Do not use direct machine-to-machine references. Use events.
- Do not write adapter code before convergence. The core must be proven correct first.
- Do not skip scenario replay after fixes. A change to one machine can break another through event cascading.
- Do not add infrastructure dependencies to machine files. Check imports — if you see `import requests`, `import sqlalchemy`, `import redis`, or anything similar in a machine file, it's wrong.
- Do not put emissions in `on_enter_*` for states that are entered by self-loops (a memory operation that self-loops on `idle` would re-fire a `cleared` event from `on_enter_idle`). Move them to `on_transition_*`.
- Do not use wall-clock time inside machines. Use `self.set_timer(...)` and let scenarios drive `advance_time`.
- Do not amend commits or force-push. Always create new commits.
