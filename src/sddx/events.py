"""Event system for inter-machine communication.

Backward-compatible port of vanilla SDD's events module. Pattern
subscriptions are surfaced as a first-class registration path; the runner
wires them up automatically when a machine class declares
``pattern_subscriptions()``.
"""

from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Event:
    """Immutable event representing a fact about what happened."""

    name: str
    payload: dict
    source_machine: str
    source_instance: str
    timestamp: float = field(default_factory=time.monotonic)
    correlation_id: str = ""

    def __post_init__(self) -> None:
        # Defensive copy of the payload dict.
        object.__setattr__(self, "payload", dict(self.payload))


class EventLog:
    """Query interface for filtering events."""

    def __init__(self, events: list[Event]) -> None:
        self._events = list(events)

    def __iter__(self):
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __getitem__(self, index: int) -> Event:
        return self._events[index]

    def filter(
        self,
        name: str | None = None,
        source_machine: str | None = None,
        source_instance: str | None = None,
        correlation_id: str | None = None,
    ) -> "EventLog":
        result = self._events
        if name is not None:
            result = [e for e in result if e.name == name]
        if source_machine is not None:
            result = [e for e in result if e.source_machine == source_machine]
        if source_instance is not None:
            result = [e for e in result if e.source_instance == source_instance]
        if correlation_id is not None:
            result = [e for e in result if e.correlation_id == correlation_id]
        return EventLog(result)

    def after(self, timestamp: float) -> "EventLog":
        return EventLog([e for e in self._events if e.timestamp > timestamp])

    def before(self, timestamp: float) -> "EventLog":
        return EventLog([e for e in self._events if e.timestamp < timestamp])

    def unique_instances(self, machine_name: str) -> set[str]:
        return {
            e.source_instance
            for e in self._events
            if e.source_machine == machine_name
        }

    def to_list(self) -> list[Event]:
        return list(self._events)


class EventBus:
    """Central hub for event emission and subscription."""

    def __init__(self, max_cascade_depth: int = 20) -> None:
        self._log: list[Event] = []
        self._subscriptions: dict[str, list[Callable[[Event], None]]] = {}
        self._pattern_subscriptions: list[tuple[str, Callable[[Event], None]]] = []
        self._max_cascade_depth = max_cascade_depth
        self._cascade_depth = 0
        self._delivery_queue: list[Event] = []
        self._delivering = False

    @property
    def log(self) -> EventLog:
        return EventLog(list(self._log))

    def emit(self, event: Event) -> None:
        self._log.append(event)
        self._delivery_queue.append(event)
        if not self._delivering:
            self._deliver_queued_events()

    def _deliver_queued_events(self) -> None:
        self._delivering = True
        try:
            while self._delivery_queue:
                self._cascade_depth += 1
                if self._cascade_depth > self._max_cascade_depth:
                    raise RuntimeError(
                        f"Event cascade exceeded maximum depth of "
                        f"{self._max_cascade_depth}. Likely a circular dependency."
                    )
                event = self._delivery_queue.pop(0)
                self._deliver_event(event)
            self._cascade_depth = 0
        finally:
            self._delivering = False

    def _deliver_event(self, event: Event) -> None:
        for handler in self._subscriptions.get(event.name, []):
            handler(event)
        for pattern, handler in self._pattern_subscriptions:
            if fnmatch.fnmatch(event.name, pattern):
                handler(event)

    def subscribe(self, event_name: str, handler: Callable[[Event], None]) -> None:
        self._subscriptions.setdefault(event_name, []).append(handler)

    def subscribe_pattern(
        self, pattern: str, handler: Callable[[Event], None]
    ) -> None:
        self._pattern_subscriptions.append((pattern, handler))

    def clear(self) -> None:
        self._log.clear()
        self._subscriptions.clear()
        self._pattern_subscriptions.clear()
        self._delivery_queue.clear()
        self._cascade_depth = 0
        self._delivering = False
