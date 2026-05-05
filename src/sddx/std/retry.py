"""Retry — one operation with bounded retry-and-backoff.

Pattern: an operation that may transiently fail and is worth retrying with
a backoff delay. Each attempt is a transition; the timer schedules the
next attempt.

Usage
-----

Subclass to declare the events that mark attempt success and failure:

.. code-block:: python

    class HTTPRetry(Retry):
        SUCCESS_EVENT = "http.response_received"
        FAILURE_EVENT = "http.request_failed"

Lifecycle:

1. Parent creates the Retry instance with context
   ``{"retry_id", "max_attempts", "base_delay", "attempts": 0}``.
2. Parent fires ``start()``, which moves to ``attempting`` and emits
   ``retry.attempt_requested`` for the workload to act on.
3. Workload responds with the success or failure event.
4. On failure, the Retry schedules a delayed re-attempt (exponential
   backoff) until ``attempts == max_attempts``, then moves to
   ``exhausted``.

Backoff: ``delay = base_delay * (2 ** (attempts - 1))``.
"""

from __future__ import annotations

from sddx.protocol import State, StateMachine


class Retry(StateMachine):
    """Bounded-retry-with-backoff machine.

    Context fields:
        retry_id (str): identifier the workload echoes in success/failure events.
        max_attempts (int): hard cap on attempts.
        base_delay (float): seconds before the second attempt; doubled each retry.
        attempts (int): mutated by the machine.
    """

    SUCCESS_EVENT: str = ""
    FAILURE_EVENT: str = ""

    pending = State(initial=True)
    attempting = State()
    backing_off = State()
    succeeded = State(final=True)
    exhausted = State(final=True)

    start = pending.to(attempting)
    record_success = attempting.to(succeeded)
    record_failure = attempting.to(backing_off) | attempting.to(exhausted)
    retry_now = backing_off.to(attempting)

    # --- Guards ---

    def guard_record_failure_to_backing_off(self, **kwargs) -> bool:
        attempts = self._context.get("attempts", 0)
        return attempts < self._context.get("max_attempts", 1)

    def guard_record_failure_to_exhausted(self, **kwargs) -> bool:
        attempts = self._context.get("attempts", 0)
        return attempts >= self._context.get("max_attempts", 1)

    # --- Side effects ---

    def on_transition_start(self) -> None:
        self._context["attempts"] = 1

    def on_enter_attempting(self, source: str) -> None:
        self.emit("retry.attempt_requested", {
            "retry_id": self._context.get("retry_id"),
            "attempt": self._context.get("attempts", 1),
        })

    def on_transition_record_failure(self) -> None:
        # Increment attempts up front so guards see the post-failure count.
        self._context["attempts"] = self._context.get("attempts", 0) + 1

    def on_enter_backing_off(self) -> None:
        if self._clock is None:
            return
        attempts = self._context.get("attempts", 1)
        base = float(self._context.get("base_delay", 1.0))
        delay = base * (2 ** (attempts - 2)) if attempts > 1 else base
        self.set_timer(
            event_name="retry.backoff_elapsed",
            delay=delay,
            payload={"retry_id": self._context.get("retry_id")},
        )

    def on_enter_succeeded(self) -> None:
        self.emit("retry.succeeded", {
            "retry_id": self._context.get("retry_id"),
            "attempts": self._context.get("attempts"),
        })

    def on_enter_exhausted(self) -> None:
        self.emit("retry.exhausted", {
            "retry_id": self._context.get("retry_id"),
            "attempts": self._context.get("attempts"),
        })

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        subs: dict[str, str] = {"retry.backoff_elapsed": "retry_now"}
        if cls.SUCCESS_EVENT:
            subs[cls.SUCCESS_EVENT] = "record_success"
        if cls.FAILURE_EVENT:
            subs[cls.FAILURE_EVENT] = "record_failure"
        return subs
