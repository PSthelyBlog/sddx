"""Virtual clock and scheduled-event support for sddx.

Replaces vanilla SDD's stubbed ``AdvanceTimeStep``. A ``VirtualClock``
holds the simulation's current time and a sorted list of pending
scheduled events. Calling ``advance(seconds)`` yields all events whose
deadline has passed; the runner is responsible for emitting them
through the EventBus.

Machines schedule timers via ``self.set_timer(name, delay, payload)``
on the StateMachine base class. The clock is injected by the runner.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScheduledEvent:
    """A pending event waiting for virtual time to advance past its deadline."""

    deadline: float
    event_name: str
    payload: dict
    source_machine: str
    source_instance: str


class VirtualClock:
    """Single source of truth for simulation time."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self._scheduled: list[ScheduledEvent] = []

    def now(self) -> float:
        return self._now

    def schedule(
        self,
        delay: float,
        event_name: str,
        payload: dict,
        source_machine: str = "",
        source_instance: str = "",
    ) -> ScheduledEvent:
        if delay < 0:
            raise ValueError(f"timer delay must be non-negative, got {delay}")
        scheduled = ScheduledEvent(
            deadline=self._now + delay,
            event_name=event_name,
            payload=dict(payload),
            source_machine=source_machine,
            source_instance=source_instance,
        )
        self._scheduled.append(scheduled)
        self._scheduled.sort(key=lambda s: s.deadline)
        return scheduled

    def advance(self, seconds: float) -> list[ScheduledEvent]:
        """Advance virtual time, returning events whose deadline has now passed.

        Events are returned in deadline order. Caller is responsible for
        emitting them via the EventBus.
        """
        if seconds < 0:
            raise ValueError(f"cannot advance backwards (got {seconds}s)")
        target = self._now + seconds
        fired: list[ScheduledEvent] = []
        remaining: list[ScheduledEvent] = []
        for scheduled in self._scheduled:
            if scheduled.deadline <= target:
                fired.append(scheduled)
            else:
                remaining.append(scheduled)
        self._scheduled = remaining
        self._now = target
        return fired

    def pending(self) -> list[ScheduledEvent]:
        """Read-only snapshot of currently pending scheduled events."""
        return list(self._scheduled)

    def reset(self) -> None:
        self._now = 0.0
        self._scheduled.clear()

    def snapshot(self) -> dict:
        return {
            "now": self._now,
            "scheduled": [
                {
                    "deadline": s.deadline,
                    "event_name": s.event_name,
                    "payload": dict(s.payload),
                    "source_machine": s.source_machine,
                    "source_instance": s.source_instance,
                }
                for s in self._scheduled
            ],
        }

    @classmethod
    def restore(cls, snapshot: dict) -> "VirtualClock":
        clock = cls(start=snapshot.get("now", 0.0))
        for s in snapshot.get("scheduled", []):
            clock._scheduled.append(ScheduledEvent(
                deadline=s["deadline"],
                event_name=s["event_name"],
                payload=dict(s.get("payload", {})),
                source_machine=s.get("source_machine", ""),
                source_instance=s.get("source_instance", ""),
            ))
        clock._scheduled.sort(key=lambda s: s.deadline)
        return clock
