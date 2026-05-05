"""Barrier — fan-in over N events sharing a correlation_id.

A parent task fans out N work items, each producing a completion event.
The Barrier waits until all N complete (or its timeout fires), then emits
``barrier.completed`` or ``barrier.timed_out``.

Usage
-----

Subclass to declare which event marks "one work item complete":

.. code-block:: python

    class ToolBatchBarrier(Barrier):
        WATCHED_EVENT = "tool.completed"

Lifecycle:

1. Parent creates the Barrier instance with context
   ``{"barrier_id", "expected", "received": 0, "timeout_seconds": ...}``.
2. Parent fires ``start()``, which schedules the timer (if any) and moves
   to the ``waiting`` state.
3. Each matching watched-event self-loops on ``waiting`` until ``received
   == expected``, at which point the barrier transitions to ``completed``.
4. If the timeout fires first, the barrier transitions to ``timed_out``.
"""

from __future__ import annotations

from sddx.protocol import State, StateMachine


class Barrier(StateMachine):
    """Fan-in barrier with optional timeout.

    Context fields:
        barrier_id (str): identifier matching the correlation_id of watched events.
        expected (int): how many events must arrive before completion.
        received (int): mutated by the machine; starts at 0.
        timeout_seconds (float | None): if set, scheduled when ``start()`` fires.

    Subclasses set ``WATCHED_EVENT`` to the event name that signals one
    unit of progress.
    """

    WATCHED_EVENT: str = ""

    pending = State(initial=True)
    waiting = State()
    completed = State(final=True)
    timed_out = State(final=True)

    start = pending.to(waiting)
    record_completion = waiting.loop() | waiting.to(completed)
    fire_timeout = waiting.to(timed_out)

    # --- Guards ---

    def guard_record_completion_to_completed(self, **kwargs) -> bool:
        received = self._context.get("received", 0) + 1
        return received >= self._context.get("expected", 1)

    def guard_record_completion_to_waiting(self, **kwargs) -> bool:
        received = self._context.get("received", 0) + 1
        return received < self._context.get("expected", 1)

    # --- Side effects ---

    def on_transition_start(self) -> None:
        timeout = self._context.get("timeout_seconds")
        if timeout is None or self._clock is None:
            return
        self.set_timer(
            event_name="barrier.timeout_fired",
            delay=float(timeout),
            payload={"barrier_id": self._context.get("barrier_id")},
        )

    def on_transition_record_completion(self) -> None:
        self._context["received"] = self._context.get("received", 0) + 1

    def on_enter_completed(self) -> None:
        self.emit("barrier.completed", {
            "barrier_id": self._context.get("barrier_id"),
            "received": self._context.get("received"),
        })

    def on_enter_timed_out(self) -> None:
        self.emit("barrier.timed_out", {
            "barrier_id": self._context.get("barrier_id"),
            "received": self._context.get("received", 0),
            "expected": self._context.get("expected"),
        })

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        subs: dict[str, str] = {"barrier.timeout_fired": "fire_timeout"}
        if cls.WATCHED_EVENT:
            subs[cls.WATCHED_EVENT] = "record_completion"
        return subs
