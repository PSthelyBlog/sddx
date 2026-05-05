"""State machine protocol for sddx.

Backward-compatible superset of vanilla SDD's protocol. Adds:

- ``State.loop()`` and ``StateGroup.loop()`` for self-loop transitions.
- Source-aware side-effect hooks: ``on_enter_X``, ``on_exit_X``, and
  ``on_transition_T`` may declare ``source`` and/or ``target`` parameters
  to receive them.
- Per-source guards: when a transition has multiple branches sharing a
  target, you can disambiguate by source via
  ``guard_T_from_SOURCE_to_TARGET``. Falls back to ``guard_T_to_TARGET``.
- ``pattern_subscriptions()`` classmethod for glob event subscriptions.
- ``telemetry_events()`` classmethod to mark events as observation-only
  (suppresses dead-letter warnings for them).
- A ``_clock`` attribute populated by the runner so machines can schedule
  delayed events via ``self.set_timer(...)``.

Vanilla machines (no parameters on hooks, no per-source guards) continue
to work unmodified.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from sddx.events import Event, EventBus
    from sddx.timing import VirtualClock


@dataclass(frozen=True)
class TransitionResult:
    """Result of attempting to fire a transition."""

    success: bool
    source: str
    target: str
    transition: str
    failure_reason: str | None = None
    events_emitted: list["Event"] = field(default_factory=list)


@dataclass(frozen=True)
class TransitionRecord:
    """Historical record of a fired transition."""

    transition: str
    source: str
    target: str
    kwargs: dict


class TransitionBranch:
    """A single source -> target branch of a transition."""

    def __init__(self, source: "State", target: "State") -> None:
        self._source_state = source
        self._target_state = target
        self.source: str = ""
        self.target: str = ""

    def _resolve_names(self) -> None:
        self.source = self._source_state._name
        self.target = self._target_state._name

    def __repr__(self) -> str:
        return f"TransitionBranch({self.source!r} -> {self.target!r})"


class Transition:
    """Descriptor representing a transition between states."""

    def __init__(self, branches: list[TransitionBranch]) -> None:
        self.branches = branches
        self.name: str = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __or__(self, other: "Transition") -> "Transition":
        return Transition(self.branches + other.branches)

    def _resolve_names(self) -> None:
        for branch in self.branches:
            branch._resolve_names()

    def get_branches_from(self, source: str) -> list[TransitionBranch]:
        return [b for b in self.branches if b.source == source]

    def get_all_sources(self) -> set[str]:
        return {b.source for b in self.branches}

    def __repr__(self) -> str:
        return f"Transition({self.name!r}, {self.branches})"


class StateGroup:
    """A group of states usable as transition sources."""

    def __init__(self, states: list["State"]) -> None:
        self._states = states

    def __or__(self, other: "State | StateGroup") -> "StateGroup":
        if isinstance(other, State):
            return StateGroup(self._states + [other])
        return StateGroup(self._states + other._states)

    def to(self, target: "State") -> Transition:
        branches = [TransitionBranch(src, target) for src in self._states]
        return Transition(branches)

    def loop(self) -> Transition:
        """Self-loop each state: ``(a|b|c).loop() == a.to(a) | b.to(b) | c.to(c)``."""
        branches = [TransitionBranch(s, s) for s in self._states]
        return Transition(branches)


class State:
    """Descriptor for declaring states in a state machine."""

    def __init__(self, *, initial: bool = False, final: bool = False) -> None:
        self.initial = initial
        self.final = final
        self._name: str = ""
        self._owner: type | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name
        self._owner = owner

    def __get__(self, obj: Any, objtype: type | None = None) -> str:
        return self._name

    def __or__(self, other: "State | StateGroup") -> StateGroup:
        if isinstance(other, State):
            return StateGroup([self, other])
        return StateGroup([self] + other._states)

    def to(self, target: "State") -> Transition:
        return Transition([TransitionBranch(self, target)])

    def loop(self) -> Transition:
        """Self-loop: ``a.loop() == a.to(a)``."""
        return Transition([TransitionBranch(self, self)])

    def __repr__(self) -> str:
        flags = []
        if self.initial:
            flags.append("initial")
        if self.final:
            flags.append("final")
        flag_str = f"({', '.join(flags)})" if flags else ""
        return f"State({self._name!r}{flag_str})"


class StateMachineMeta(type):
    """Metaclass that collects state and transition declarations."""

    def __new__(
        mcs, name: str, bases: tuple[type, ...], namespace: dict[str, Any]
    ) -> "StateMachineMeta":
        cls = super().__new__(mcs, name, bases, namespace)

        if name == "StateMachine" and not any(
            isinstance(b, StateMachineMeta) for b in bases
        ):
            return cls

        states: dict[str, State] = {}
        transitions: dict[str, Transition] = {}

        for base in reversed(cls.__mro__):
            if base is object:
                continue
            for attr_name, attr_value in vars(base).items():
                if isinstance(attr_value, State):
                    states[attr_name] = attr_value
                elif isinstance(attr_value, Transition):
                    transitions[attr_name] = attr_value

        cls._states = states
        cls._transitions = transitions

        for transition in transitions.values():
            transition._resolve_names()

        initial_states = [s for s in states.values() if s.initial]
        if len(initial_states) == 0 and states:
            raise TypeError(f"{name} must have exactly one initial state")
        if len(initial_states) > 1:
            raise TypeError(f"{name} has multiple initial states")

        final_states = [s for s in states.values() if s.final]
        if len(final_states) == 0 and states:
            raise TypeError(f"{name} must have at least one final state")

        return cls


def _hook_kwargs(method: Any, *, source: str, target: str) -> dict[str, str]:
    """Inspect the hook signature and return only the kwargs it accepts."""
    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):
        return {}
    accepted: dict[str, str] = {}
    params = sig.parameters
    if "source" in params:
        accepted["source"] = source
    if "target" in params:
        accepted["target"] = target
    return accepted


class StateMachine(metaclass=StateMachineMeta):
    """Base class for all state machines."""

    _states: ClassVar[dict[str, State]] = {}
    _transitions: ClassVar[dict[str, Transition]] = {}

    def __init__(
        self,
        context: dict | None = None,
        event_bus: "EventBus | None" = None,
        instance_id: str = "",
        clock: "VirtualClock | None" = None,
    ) -> None:
        self._context = dict(context) if context else {}
        self._event_bus = event_bus
        self._instance_id = instance_id
        self._clock = clock
        self._history: list[TransitionRecord] = []
        self._pending_events: list["Event"] = []

        for name, state in self._states.items():
            if state.initial:
                self._current_state = name
                break

    @property
    def context(self) -> dict:
        return dict(self._context)

    @property
    def current_state(self) -> str:
        return self._current_state

    @property
    def is_final(self) -> bool:
        state = self._states.get(self._current_state)
        return state.final if state else False

    @property
    def available_transitions(self) -> list[str]:
        if self.is_final:
            return []
        result = []
        for name, transition in self._transitions.items():
            if self._current_state in transition.get_all_sources():
                result.append(name)
        return result

    @property
    def all_states(self) -> list[str]:
        return list(self._states.keys())

    @property
    def all_transitions(self) -> list[dict]:
        result = []
        for name, transition in self._transitions.items():
            for branch in transition.branches:
                result.append({
                    "name": name,
                    "source": branch.source,
                    "target": branch.target,
                })
        return result

    @property
    def history(self) -> list[TransitionRecord]:
        return list(self._history)

    def fire(self, transition_name: str, **kwargs: Any) -> TransitionResult:
        source = self._current_state

        transition = self._transitions.get(transition_name)
        if transition is None:
            return TransitionResult(
                success=False, source=source, target=source,
                transition=transition_name, failure_reason="unknown_transition",
            )

        branches = transition.get_branches_from(source)
        if not branches:
            return TransitionResult(
                success=False, source=source, target=source,
                transition=transition_name, failure_reason="invalid_source_state",
            )

        self._context.update(kwargs)

        target = None
        for branch in branches:
            # Per-source guard takes precedence; fall back to target-only.
            per_source_name = (
                f"guard_{transition_name}_from_{branch.source}_to_{branch.target}"
            )
            target_only_name = f"guard_{transition_name}_to_{branch.target}"
            guard = getattr(self, per_source_name, None) or getattr(
                self, target_only_name, None
            )
            if guard is None:
                target = branch.target
                break
            if guard(**kwargs):
                target = branch.target
                break

        if target is None:
            return TransitionResult(
                success=False, source=source, target=source,
                transition=transition_name, failure_reason="no_guard_passed",
            )

        self._pending_events = []

        self._invoke_hook(f"on_exit_{source}", source=source, target=target)
        self._invoke_hook(
            f"on_transition_{transition_name}", source=source, target=target,
        )
        self._current_state = target
        self._invoke_hook(f"on_enter_{target}", source=source, target=target)

        self._history.append(TransitionRecord(
            transition=transition_name, source=source, target=target,
            kwargs=dict(kwargs),
        ))

        events_emitted = list(self._pending_events)
        if self._event_bus:
            for event in events_emitted:
                self._event_bus.emit(event)
        self._pending_events = []

        return TransitionResult(
            success=True, source=source, target=target,
            transition=transition_name, events_emitted=events_emitted,
        )

    def _invoke_hook(self, name: str, *, source: str, target: str) -> None:
        method = getattr(self, name, None)
        if method is None:
            return
        method(**_hook_kwargs(method, source=source, target=target))

    def emit(self, name: str, payload: dict) -> None:
        from sddx.events import Event
        import time

        event = Event(
            name=name,
            payload=payload,
            source_machine=self.__class__.__name__,
            source_instance=self._instance_id,
            timestamp=self._clock.now() if self._clock else time.monotonic(),
            correlation_id=self._context.get("correlation_id", ""),
        )
        self._pending_events.append(event)

    def set_timer(self, event_name: str, delay: float, payload: dict) -> None:
        """Schedule an event to fire after ``delay`` seconds of virtual time.

        Requires the runner to have wired a clock into this instance. If no
        clock is attached, this is a no-op (caller is responsible for verifying).
        """
        if self._clock is None:
            raise RuntimeError(
                f"{self.__class__.__name__}({self._instance_id!r}) has no clock; "
                "attach one via the runner before calling set_timer()."
            )
        self._clock.schedule(
            delay=delay,
            event_name=event_name,
            payload=payload,
            source_machine=self.__class__.__name__,
            source_instance=self._instance_id,
        )

    def snapshot(self) -> dict:
        return {
            "state": self._current_state,
            "context": dict(self._context),
            "history": [
                {
                    "transition": r.transition,
                    "source": r.source,
                    "target": r.target,
                    "kwargs": r.kwargs,
                }
                for r in self._history
            ],
        }

    @classmethod
    def restore(
        cls,
        snapshot: dict,
        event_bus: "EventBus | None" = None,
        instance_id: str = "",
        clock: "VirtualClock | None" = None,
    ) -> "StateMachine":
        machine = cls(
            context=snapshot.get("context", {}),
            event_bus=event_bus,
            instance_id=instance_id,
            clock=clock,
        )
        machine._current_state = snapshot["state"]
        machine._history = [
            TransitionRecord(
                transition=r["transition"],
                source=r["source"],
                target=r["target"],
                kwargs=r["kwargs"],
            )
            for r in snapshot.get("history", [])
        ]
        return machine

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        """Map event names to transition names."""
        return {}

    @classmethod
    def pattern_subscriptions(cls) -> dict[str, str]:
        """Map glob event patterns to transition names.

        Example: ``{"calc.memory_*_requested": "record_request"}``.
        """
        return {}

    @classmethod
    def telemetry_events(cls) -> set[str]:
        """Events emitted by this machine that are observation-only.

        These are excluded from dead-letter warnings during structural checks
        even when no machine subscribes to them.
        """
        return set()
