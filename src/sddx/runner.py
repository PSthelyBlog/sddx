"""Simulation runner for sddx.

Backward-compatible port of vanilla SDD's runner with these additions:

- Pattern subscriptions are wired up automatically when a machine class
  declares ``pattern_subscriptions()``.
- Telemetry-marked events (declared via ``telemetry_events()``) are
  excluded from dead-letter warnings.
- Each machine instance receives a shared ``VirtualClock``.
- The runner exposes ``advance_time(seconds)`` to drive scheduled events
  through the EventBus.
- ``snapshot()``/``restore()`` capture and restore the full simulation
  state: every instance, the event log, and the virtual clock.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from sddx.events import Event, EventBus, EventLog
from sddx.protocol import StateMachine, TransitionRecord, TransitionResult
from sddx.timing import VirtualClock


@dataclass
class StructuralReport:
    """Results of structural analysis on registered machines."""

    machines_analyzed: list[str] = field(default_factory=list)
    total_states: int = 0
    total_transitions: int = 0
    unreachable_states: dict[str, list[str]] = field(default_factory=dict)
    terminal_states: dict[str, list[str]] = field(default_factory=dict)
    dead_letters: list[str] = field(default_factory=list)
    phantom_subscriptions: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        has_unreachable = any(states for states in self.unreachable_states.values())
        has_terminal = any(states for states in self.terminal_states.values())
        return (
            not has_unreachable
            and not has_terminal
            and not self.dead_letters
            and not self.phantom_subscriptions
        )


@dataclass
class StepError:
    step_index: int
    machine: str
    instance: str
    transition: str
    error_type: str
    message: str
    machine_state_at_error: str


@dataclass
class StepResult:
    trigger: str
    transitions_fired: list[TransitionRecord] = field(default_factory=list)
    events_emitted: list[Event] = field(default_factory=list)
    cascade_depth: int = 0
    errors: list[StepError] = field(default_factory=list)


class SimulationRunner:
    """Environment for operating state machines during simulation."""

    def __init__(self) -> None:
        self._event_bus = EventBus()
        self._clock = VirtualClock()
        self._machine_classes: dict[type, type[StateMachine]] = {}
        self._resolvers: dict[type, Callable[[Event], str]] = {}
        self._instances: dict[tuple[type, str], StateMachine] = {}

        self._current_step_result: StepResult | None = None
        self._cascade_depth: int = 0
        self._step_index: int = 0

    # -- Properties --

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def event_log(self) -> EventLog:
        return self._event_bus.log

    @property
    def clock(self) -> VirtualClock:
        return self._clock

    # -- Registration --

    def register(self, machine_class: type[StateMachine]) -> None:
        """Register a machine class and wire up its subscriptions."""
        self._machine_classes[machine_class] = machine_class

        subscriptions = machine_class.subscriptions()
        for event_name, transition_name in subscriptions.items():
            self._subscribe_exact(machine_class, event_name, transition_name)

        # sddx: pattern subscriptions are wired up too, if declared.
        pattern_subs = getattr(machine_class, "pattern_subscriptions", lambda: {})()
        for pattern, transition_name in pattern_subs.items():
            self._subscribe_pattern(machine_class, pattern, transition_name)

    def register_resolver(
        self,
        machine_class: type[StateMachine],
        resolver: Callable[[Event], str],
    ) -> None:
        self._resolvers[machine_class] = resolver

    def _subscribe_exact(
        self,
        machine_class: type[StateMachine],
        event_name: str,
        transition_name: str,
    ) -> None:
        handler = self._make_subscription_handler(machine_class, transition_name)
        self._event_bus.subscribe(event_name, handler)

    def _subscribe_pattern(
        self,
        machine_class: type[StateMachine],
        pattern: str,
        transition_name: str,
    ) -> None:
        handler = self._make_subscription_handler(machine_class, transition_name)
        self._event_bus.subscribe_pattern(pattern, handler)

    def _make_subscription_handler(
        self,
        machine_class: type[StateMachine],
        transition_name: str,
    ) -> Callable[[Event], None]:
        def handler(event: Event) -> None:
            resolver = self._resolvers.get(machine_class)
            if resolver is None:
                return
            resolved_id = resolver(event)
            if resolved_id is None:
                return
            instance = self.get(machine_class, resolved_id)
            if instance is None:
                instance = self.create(machine_class, resolved_id, dict(event.payload))
            self._fire_internal(
                machine_class, resolved_id, transition_name, **event.payload
            )

        return handler

    # -- Instance lifecycle --

    def create(
        self,
        machine_class: type[StateMachine],
        instance_id: str,
        context: dict | None = None,
    ) -> StateMachine:
        instance = machine_class(
            context=context,
            event_bus=self._event_bus,
            instance_id=instance_id,
            clock=self._clock,
        )
        self._instances[(machine_class, instance_id)] = instance
        return instance

    def get(
        self,
        machine_class: type[StateMachine],
        instance_id: str,
    ) -> StateMachine | None:
        return self._instances.get((machine_class, instance_id))

    # -- Firing transitions --

    def fire(
        self,
        instance_id: str,
        machine_class: type[StateMachine],
        transition: str,
        **kwargs: Any,
    ) -> StepResult:
        self._current_step_result = StepResult(trigger=f"fire:{transition}")
        self._cascade_depth = 0
        self._step_index = 0

        events_before = len(self._event_bus._log)
        self._fire_internal(machine_class, instance_id, transition, **kwargs)
        events_after = len(self._event_bus._log)
        if events_after > events_before:
            self._current_step_result.events_emitted = list(
                self._event_bus._log[events_before:events_after]
            )
        self._current_step_result.cascade_depth = self._cascade_depth

        result = self._current_step_result
        self._current_step_result = None
        return result

    def _fire_internal(
        self,
        machine_class: type[StateMachine],
        target_instance_id: str,
        transition: str,
        **kwargs: Any,
    ) -> TransitionResult | None:
        instance = self.get(machine_class, target_instance_id)
        if instance is None:
            if self._current_step_result:
                self._current_step_result.errors.append(StepError(
                    step_index=self._step_index,
                    machine=machine_class.__name__,
                    instance=target_instance_id,
                    transition=transition,
                    error_type="instance_not_found",
                    message=(
                        f"No instance found for "
                        f"{machine_class.__name__}('{target_instance_id}')"
                    ),
                    machine_state_at_error="",
                ))
            return None

        self._cascade_depth += 1
        self._step_index += 1

        result = instance.fire(transition, **kwargs)

        if result.success:
            if self._current_step_result:
                self._current_step_result.transitions_fired.append(TransitionRecord(
                    transition=result.transition,
                    source=result.source,
                    target=result.target,
                    kwargs=dict(kwargs),
                ))
        else:
            if self._current_step_result:
                self._current_step_result.errors.append(StepError(
                    step_index=self._step_index,
                    machine=machine_class.__name__,
                    instance=target_instance_id,
                    transition=transition,
                    error_type=result.failure_reason or "unknown",
                    message=self._format_error_message(
                        result.failure_reason, machine_class, instance, transition
                    ),
                    machine_state_at_error=instance.current_state,
                ))

        return result

    @staticmethod
    def _format_error_message(
        failure_reason: str | None,
        machine_class: type,
        instance: StateMachine,
        transition: str,
    ) -> str:
        if failure_reason == "unknown_transition":
            return (
                f"Transition '{transition}' does not exist on "
                f"{machine_class.__name__}"
            )
        if failure_reason == "invalid_source_state":
            return (
                f"Transition '{transition}' cannot fire from state "
                f"'{instance.current_state}' on {machine_class.__name__}"
            )
        if failure_reason == "no_guard_passed":
            return (
                f"No guard passed for transition '{transition}' from state "
                f"'{instance.current_state}' on {machine_class.__name__}"
            )
        return f"Unknown error firing '{transition}' on {machine_class.__name__}"

    # -- Time advancement --

    def advance_time(self, seconds: float) -> list[Event]:
        """Advance virtual time and emit any scheduled events that fired.

        Returns the list of events emitted to the bus during this advance.
        """
        fired = self._clock.advance(seconds)
        emitted: list[Event] = []
        now = self._clock.now()
        for scheduled in fired:
            event = Event(
                name=scheduled.event_name,
                payload=scheduled.payload,
                source_machine=scheduled.source_machine,
                source_instance=scheduled.source_instance,
                timestamp=now,
            )
            self._event_bus.emit(event)
            emitted.append(event)
        return emitted

    # -- Reset / snapshot --

    def reset(self) -> None:
        """Destroy all instances, clear the event log and the clock.

        Retains machine class registrations and resolvers (and the existing
        EventBus subscriptions, since handlers are bound to this runner).
        """
        self._instances.clear()
        self._event_bus._log.clear()
        self._event_bus._delivery_queue.clear()
        self._event_bus._cascade_depth = 0
        self._event_bus._delivering = False
        self._clock.reset()

    def snapshot(self) -> dict:
        """Capture the entire simulation state: instances, event log, clock.

        Machine class registrations and EventBus subscriptions are NOT included
        — those are reconstructed by re-registering the classes on a fresh
        runner before calling ``restore()``.
        """
        return {
            "version": 1,
            "clock": self._clock.snapshot(),
            "instances": [
                {
                    "machine": cls.__name__,
                    "instance_id": instance_id,
                    "snapshot": instance.snapshot(),
                }
                for (cls, instance_id), instance in self._instances.items()
            ],
            "event_log": [
                {
                    "name": e.name,
                    "payload": dict(e.payload),
                    "source_machine": e.source_machine,
                    "source_instance": e.source_instance,
                    "timestamp": e.timestamp,
                    "correlation_id": e.correlation_id,
                }
                for e in self._event_bus._log
            ],
        }

    def restore(self, snapshot: dict) -> None:
        """Restore simulation state into this runner.

        The runner must already have machine classes registered. Existing
        instances are discarded and replaced from the snapshot.
        """
        if snapshot.get("version") != 1:
            raise ValueError(
                f"Unsupported snapshot version: {snapshot.get('version')!r}"
            )

        self._instances.clear()
        self._event_bus._log.clear()
        self._event_bus._delivery_queue.clear()
        self._event_bus._cascade_depth = 0
        self._event_bus._delivering = False

        self._clock = VirtualClock.restore(snapshot.get("clock", {}))

        for instance_data in snapshot.get("instances", []):
            machine_name = instance_data["machine"]
            instance_id = instance_data["instance_id"]
            machine_class = self.get_machine_class(machine_name)
            if machine_class is None:
                raise ValueError(
                    f"Cannot restore: machine class {machine_name!r} not registered"
                )
            instance = machine_class.restore(
                instance_data["snapshot"],
                event_bus=self._event_bus,
                instance_id=instance_id,
                clock=self._clock,
            )
            self._instances[(machine_class, instance_id)] = instance

        for event_data in snapshot.get("event_log", []):
            self._event_bus._log.append(Event(
                name=event_data["name"],
                payload=dict(event_data.get("payload", {})),
                source_machine=event_data.get("source_machine", ""),
                source_instance=event_data.get("source_instance", ""),
                timestamp=event_data.get("timestamp", 0.0),
                correlation_id=event_data.get("correlation_id", ""),
            ))

    # -- Inspection --

    def machine_states(self) -> dict[tuple[type, str], str]:
        return {
            key: instance.current_state for key, instance in self._instances.items()
        }

    def active_instances(self) -> list[tuple[type, str]]:
        return [
            key for key, instance in self._instances.items() if not instance.is_final
        ]

    def final_instances(self) -> list[tuple[type, str]]:
        return [key for key, instance in self._instances.items() if instance.is_final]

    def get_machine_class(self, name: str) -> type[StateMachine] | None:
        for cls in self._machine_classes:
            if cls.__name__ == name:
                return cls
        return None

    # -- Structural validation --

    def reachability(self, machine_class: type[StateMachine]) -> set[str]:
        initial_state: str | None = None
        for name, state in machine_class._states.items():
            if state.initial:
                initial_state = name
                break
        if initial_state is None:
            return set()

        visited: set[str] = set()
        queue: deque[str] = deque([initial_state])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for transition in machine_class._transitions.values():
                for branch in transition.branches:
                    if branch.source == current and branch.target not in visited:
                        queue.append(branch.target)
        return visited

    def dead_states(self, machine_class: type[StateMachine]) -> set[str]:
        all_states = set(machine_class._states.keys())
        return all_states - self.reachability(machine_class)

    def termination_issues(self, machine_class: type[StateMachine]) -> set[str]:
        final_states: set[str] = {
            name for name, state in machine_class._states.items() if state.final
        }
        if not final_states:
            return set(machine_class._states.keys())

        can_reach_final: set[str] = set()
        queue: deque[str] = deque(final_states)
        while queue:
            current = queue.popleft()
            if current in can_reach_final:
                continue
            can_reach_final.add(current)
            for transition in machine_class._transitions.values():
                for branch in transition.branches:
                    if branch.target == current and branch.source not in can_reach_final:
                        queue.append(branch.source)

        return {
            name for name, state in machine_class._states.items()
            if not state.final and name not in can_reach_final
        }

    def _find_emitted_events(self, machine_class: type[StateMachine]) -> set[str]:
        """Inspect side-effect hooks for `self.emit(...)` and `self.set_timer(...)`.

        Both are sources of events on the bus: `emit` is direct, `set_timer`
        schedules a deferred event whose name is the first argument. Treating
        only `emit` calls as emissions made timer-scheduled events look like
        phantom subscriptions.
        """
        emitted_events: set[str] = set()
        method_prefixes = ("on_enter_", "on_exit_", "on_transition_")
        for attr_name in dir(machine_class):
            if not any(attr_name.startswith(p) for p in method_prefixes):
                continue
            method = getattr(machine_class, attr_name, None)
            if method is None or not callable(method):
                continue
            try:
                source = inspect.getsource(method)
                source = textwrap.dedent(source)
                tree = ast.parse(source)
            except (OSError, TypeError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "self"):
                    continue
                event_name = self._extract_event_name(node)
                if event_name is not None:
                    emitted_events.add(event_name)
        return emitted_events

    @staticmethod
    def _extract_event_name(node: ast.Call) -> str | None:
        """Pull the event-name string literal out of a self.emit / self.set_timer call.

        Returns None if the method isn't one we track or the name isn't a
        statically-resolvable string literal (dynamically-named events can't
        be checked statically, so we silently skip them).
        """
        attr = node.func.attr  # type: ignore[union-attr]
        if attr == "emit":
            literal = node.args[0] if node.args else None
        elif attr == "set_timer":
            # set_timer(event_name=..., delay=..., payload=...) — first
            # positional or the `event_name` keyword.
            literal = node.args[0] if node.args else None
            if literal is None:
                for kw in node.keywords:
                    if kw.arg == "event_name":
                        literal = kw.value
                        break
        else:
            return None
        if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
            return literal.value
        return None

    def _telemetry_events(self) -> set[str]:
        """Aggregate telemetry events declared by all registered machines."""
        result: set[str] = set()
        for machine_class in self._machine_classes:
            getter = getattr(machine_class, "telemetry_events", None)
            if getter is None:
                continue
            try:
                result.update(getter())
            except TypeError:
                # Wasn't a classmethod; skip.
                continue
        return result

    def _all_subscribed(self) -> set[str]:
        """All event names + glob patterns that any machine subscribes to.

        Pattern entries are returned as-is (with wildcards). Used for dead-letter
        and phantom-subscription analysis: an emission matching any subscribed
        pattern is not a dead letter.
        """
        names: set[str] = set()
        patterns: set[str] = set()
        for machine_class in self._machine_classes:
            names.update(machine_class.subscriptions().keys())
            pattern_getter = getattr(machine_class, "pattern_subscriptions", None)
            if pattern_getter is not None:
                patterns.update(pattern_getter().keys())
        return names | patterns

    def dead_letters(self) -> list[str]:
        """Events emitted but not subscribed to (excluding telemetry events)."""
        import fnmatch
        all_emitted: set[str] = set()
        for machine_class in self._machine_classes:
            all_emitted.update(self._find_emitted_events(machine_class))

        subscribed = self._all_subscribed()
        telemetry = self._telemetry_events()

        dead = []
        for name in sorted(all_emitted):
            if name in telemetry:
                continue
            matched = name in subscribed or any(
                "*" in s and fnmatch.fnmatch(name, s) for s in subscribed
            )
            if not matched:
                dead.append(name)
        return dead

    def phantom_subscriptions(self) -> list[str]:
        """Events subscribed to but never emitted."""
        import fnmatch
        all_emitted: set[str] = set()
        for machine_class in self._machine_classes:
            all_emitted.update(self._find_emitted_events(machine_class))

        subscribed_names: set[str] = set()
        subscribed_patterns: set[str] = set()
        for machine_class in self._machine_classes:
            subscribed_names.update(machine_class.subscriptions().keys())
            pattern_getter = getattr(machine_class, "pattern_subscriptions", None)
            if pattern_getter is not None:
                subscribed_patterns.update(pattern_getter().keys())

        phantoms = sorted(subscribed_names - all_emitted)
        # Patterns are phantoms only if they match nothing emitted.
        for pattern in sorted(subscribed_patterns):
            if not any(fnmatch.fnmatch(e, pattern) for e in all_emitted):
                phantoms.append(pattern)
        return phantoms

    def check(self) -> StructuralReport:
        report = StructuralReport()
        for machine_class in self._machine_classes:
            machine_name = machine_class.__name__
            report.machines_analyzed.append(machine_name)
            report.total_states += len(machine_class._states)
            report.total_transitions += len(machine_class._transitions)

            dead = self.dead_states(machine_class)
            if dead:
                report.unreachable_states[machine_name] = sorted(dead)

            terminal = self.termination_issues(machine_class)
            if terminal:
                report.terminal_states[machine_name] = sorted(terminal)

        report.dead_letters = self.dead_letters()
        report.phantom_subscriptions = self.phantom_subscriptions()
        return report
