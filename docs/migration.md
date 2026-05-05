# Migrating from vanilla SDD

sddx is a strict superset. Existing machines work unchanged; the migration is opt-in, file-by-file.

## Step 1: change the import

```diff
- from sdd.protocol import State, StateMachine
+ from sddx import State, StateMachine
```

That's it. Your machine continues to work — same hooks, same guards, same scenarios. Run convergence to confirm.

## Step 2 (optional): adopt the new affordances

### Self-loops with `.loop()`

```diff
- record_input = recording.to(recording)
- record_compute = recording.to(recording)
- # ... eight more lines like this ...
+ record_input = recording.loop()
+ record_compute = recording.loop()
+ # one keyword shorter per line
```

For multi-source self-loops:

```diff
- request_memory_set = (
-     idle.to(idle)
-     | entering_first.to(entering_first)
-     | operator_pending.to(operator_pending)
-     | entering_second.to(entering_second)
-     | result_shown.to(result_shown)
- )
+ _stable = idle | entering_first | operator_pending | entering_second | result_shown
+ request_memory_set = _stable.loop()
```

### Source-aware hooks

If your hook ever needed to know what state it came from, it probably looked like:

```python
# vanilla — broken on on_enter, since history isn't appended yet
def on_enter_idle(self):
    if self._history and self._history[-1].transition == "clear":
        self.emit("calc.cleared", ...)
```

In sddx, ask the framework directly:

```python
def on_enter_idle(self, source: str) -> None:
    if source != "":  # explicit; works on every entry
        self.emit("calc.cleared", ...)
```

For transitions with multiple branches (e.g. `compute → result_shown | error`), `target` resolves the same problem inside `on_transition_*`:

```python
def on_transition_compute(self, target: str) -> None:
    if target == "error":
        self._context["error_reason"] = "Division by zero"
        return
    # ... happy-path math ...
```

### Per-source guards

If a guard had to inspect `self._current_state`, you can split it:

```diff
- def guard_select_operator_to_operator_pending(self, **kwargs):
-     if self._current_state != "entering_second":
-         return True
-     return not self._would_divide_by_zero()
+ def guard_select_operator_from_entering_first_to_operator_pending(self, **kwargs):
+     return True
+ def guard_select_operator_from_entering_second_to_operator_pending(self, **kwargs):
+     return not self._would_divide_by_zero()
```

The framework tries `from_SOURCE_to_TARGET` first, then falls back to the target-only form. Mix and match.

### Pattern subscriptions

Five explicit subscriptions:

```diff
- @classmethod
- def subscriptions(cls):
-     return {
-         "calc.memory_set_requested": "record_request",
-         "calc.memory_add_requested": "record_request",
-         "calc.memory_subtract_requested": "record_request",
-         "calc.memory_recall_requested": "record_request",
-         "calc.memory_clear_requested": "record_request",
-         # ... others ...
-     }
+ @classmethod
+ def subscriptions(cls):
+     return { ... others ... }
+
+ @classmethod
+ def pattern_subscriptions(cls):
+     return {"calc.memory_*_requested": "record_request"}
```

The runner wires both up at registration time.

### Telemetry events

If you have observation-only events (traces, metrics) and your structural check complains about dead letters:

```python
@classmethod
def telemetry_events(cls) -> set[str]:
    return {"trace.span_started", "trace.span_ended"}
```

### Working `advance_time`

Vanilla SDD's `advance_time` step prints a warning and does nothing. In sddx, it actually advances `runner.clock`:

```yaml
steps:
  - fire: Worker("w1").start
  - advance_time: 30          # 30 seconds
  - advance_time: { minutes: 5 }
  - assert:
      states:
        Worker("w1"): timed_out
```

Pair it with `self.set_timer(name, delay, payload)` from inside a machine to test timeout paths deterministically.

### Working context assertions

```yaml
expect:
  context:
    Counter("c1"):
      count: 3
      last_seen: "2026-05-05"
```

Vanilla SDD warned and skipped these. sddx evaluates them.

## Step 3: adopt the project CLI

Drop `run_simulation.py`. Add `sddx_project.py`:

```python
from machines.calculator import Calculator
from machines.operation_log import OperationLog

def register_resolvers(runner) -> None:
    by_id = lambda e: e.payload.get("session_id", "")
    runner.register_resolver(Calculator, by_id)
    runner.register_resolver(OperationLog, by_id)
```

Then:

```bash
sddx -C my-project converge
```

The CLI auto-discovers `machines/`, `scenarios/**/*.yaml`, and `invariants/`.

## What hasn't changed

- `subscriptions()` classmethod — same shape and semantics.
- Guard naming convention `guard_T_to_TARGET` — still works as the fallback.
- `Event`, `EventLog`, `EventBus` API — same except `subscribe_pattern` is now used by the runner.
- Scenario YAML schema — same; adds working `advance_time` and `context` keys.
- `snapshot()` / `restore()` on machines — still works; runner-level versions are additions.

## Known incompatibilities

None intentional. If you hit one, please report — sddx aims to be a drop-in superset.
