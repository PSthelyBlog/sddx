# Command-Line Interface

`python -m sddx` is the project-level CLI. It auto-discovers a project layout and runs convergence checks without any project-specific Python entry point.

```
sddx [-C PROJECT_ROOT] {check | run [SCENARIO] | converge}
```

## Project layout

The CLI looks for the following directories under the project root (defaulting to the current working directory):

```
my-project/
├── machines/         # Auto-discovered: every *.py is imported, every StateMachine subclass is registered
├── scenarios/        # Auto-discovered: every *.yaml / *.yml is parsed
├── invariants/       # Auto-discovered: every *.py exposing invariant_* functions is loaded
└── sddx_project.py   # Optional: defines register_resolvers(runner) for cross-machine wiring
```

Only `machines/` is required. `scenarios/` and `invariants/` are needed for `run` and `converge` respectively.

## Commands

### `check` — Layer 1 only

Runs structural analysis: reachability, termination, dead letters, phantom subscriptions.

```bash
$ python -m sddx -C my-project check
Machines analyzed: ['Calculator', 'MemoryRegister', 'OperationLog']
Total states: 12, transitions: 27
Structural valid: True
```

Exits 0 if the report is valid, non-zero otherwise. A failed check prints the failure categories (`unreachable`, `terminal`, `dead letters`, `phantom subs`) and returns the diagnostic to stderr.

### `run [SCENARIO]` — Layer 2 only

Runs all scenarios under `scenarios/`, or a specific file/directory if provided.

```bash
$ python -m sddx -C my-project run
[PASS] simple_addition: 2 + 3 = 5
[PASS] divide_by_zero_compute: 5 / 0 = should land in error
[FAIL] memory_recall: After MS, MR should reload the value
    failed at step 7: State assertion failed for Calculator("s1"): expected 'entering_first', got 'idle'

13/14 scenarios passed
```

Run a single scenario file:

```bash
$ python -m sddx -C my-project run scenarios/happy_path/simple_addition.yaml
```

Run a sub-directory:

```bash
$ python -m sddx -C my-project run scenarios/failure
```

Exits 0 if all scenarios pass, non-zero otherwise. Invariants are NOT evaluated in `run` mode.

### `converge` — All three layers

Runs structural checks first; if those pass, runs every scenario with every invariant attached.

```bash
$ python -m sddx -C my-project converge
Layer 1: structural OK
[PASS] simple_addition (invariants: 9/9)
[PASS] divide_by_zero_compute (invariants: 9/9)
[PASS] memory_recall_into_operand (invariants: 9/9)
...

14/14 scenarios converged
```

This is the canonical CI command. Exits 0 only when:

- All structural checks pass.
- Every scenario passes.
- Every invariant passes for every scenario.

## The `-C` flag

`-C PATH` sets the project root for auto-discovery. Without it, the current working directory is used:

```bash
# These two are equivalent:
$ cd my-project && python -m sddx converge
$ python -m sddx -C my-project converge
```

The CLI prepends the project root to `sys.path` so `machines.foo`, `invariants.bar`, and `sddx_project.py`'s imports all resolve via natural Python imports — important so that a class loaded from `machines/x.py` is *the same class* as the one referenced in `sddx_project.py`'s `from machines.x import X`. (If you've used vanilla SDD's runner, this is the bug that motivates the project config pattern.)

## `sddx_project.py` — the project config

Auto-discovery picks up machine classes, but it can't infer which machine should receive which event. That requires resolvers, which are project-specific. Define them in `sddx_project.py` (the CLI also accepts `project.py` or `conftest_sddx.py`):

```python
# my-project/sddx_project.py
from machines.calculator import Calculator
from machines.memory_register import MemoryRegister
from machines.operation_log import OperationLog


def register_resolvers(runner) -> None:
    by_session = lambda e: e.payload.get("session_id", "")
    runner.register_resolver(Calculator, by_session)
    runner.register_resolver(MemoryRegister, by_session)
    runner.register_resolver(OperationLog, by_session)
```

The CLI loads this file (if present) after registering all discovered machines and calls `register_resolvers(runner)`. It's the one piece of glue you can't auto-derive.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | All requested checks passed. |
| `1` | At least one check failed (structural / scenario / invariant). |
| Other | Python-level error (bad YAML, import error, etc.). |

The CLI is designed to be CI-friendly. Wire it into pre-commit, GitHub Actions, or any pipeline that fails on non-zero exit:

```yaml
# .github/workflows/sddx.yml
- name: Convergence
  run: python -m sddx -C . converge
```

## Programmatic equivalent

Everything the CLI does is also available via the Python API. If you need finer control (custom invariant ordering, programmatic scenario filtering, etc.), use the runner directly:

```python
from sddx import SimulationRunner, ScenarioParser, ScenarioRunner

runner = SimulationRunner()
runner.register(MyMachine)
# ... resolvers, etc.

scen_runner = ScenarioRunner(runner)
scenarios = ScenarioParser().parse_directory("scenarios")
for scenario in scenarios:
    result = scen_runner.run(scenario, invariants=[my_invariant])
    if not result.passed:
        print(f"FAIL: {result.scenario_name}: {result.failure_reason}")
```

The CLI is a convenience over this API, not a separate code path.
